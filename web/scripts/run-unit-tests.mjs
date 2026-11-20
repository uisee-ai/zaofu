import {
  access,
  mkdtemp,
  readdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const testsRoot = path.join(webRoot, "tests");
const requested = process.argv.slice(2);
const tests = requested.length
  ? requested.map((item) => path.resolve(webRoot, item))
  : (await readdir(testsRoot))
      .filter((item) => item.endsWith(".test.ts"))
      .sort()
      .map((item) => path.join(testsRoot, item));

if (tests.length === 0) {
  console.error("No TypeScript unit tests selected.");
  process.exit(2);
}

const output = await mkdtemp(path.join(tmpdir(), "zf-web-unit-"));
const tsc = path.join(webRoot, "node_modules", ".bin", "tsc");

try {
  await access(tsc).catch(() => {
    throw new Error("TypeScript compiler not found; run `npm ci` in web/.");
  });
  const compile = spawnSync(
    tsc,
    [
      "--module",
      "ESNext",
      "--moduleResolution",
      "Bundler",
      "--target",
      "ES2022",
      "--lib",
      "DOM,DOM.Iterable,ES2022",
      "--resolveJsonModule",
      "--skipLibCheck",
      "--strict",
      "--outDir",
      output,
      "--rootDir",
      webRoot,
      ...tests,
    ],
    { cwd: webRoot, stdio: "inherit" },
  );
  if (compile.error) {
    throw compile.error;
  }
  if (compile.status !== 0) {
    throw new Error(`TypeScript test compilation failed (${compile.status ?? 1})`);
  }

  await writeFile(
    path.join(output, "package.json"),
    '{"type":"module"}\n',
    "utf8",
  );
  await symlink(path.join(webRoot, "node_modules"), path.join(output, "node_modules"), "dir");

  for (const source of tests) {
    const relative = path.relative(webRoot, source).replace(/\.ts$/, ".js");
    const run = spawnSync(process.execPath, [path.join(output, relative)], {
      cwd: webRoot,
      stdio: "inherit",
    });
    if (run.status !== 0) {
      throw new Error(`${relative} failed (${run.status ?? 1})`);
    }
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
} finally {
  await rm(output, { recursive: true, force: true });
}
