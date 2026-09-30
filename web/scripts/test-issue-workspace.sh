#!/usr/bin/env bash
# 可选 Docker 入口：镜像版本严格匹配已安装的项目 Playwright。
set -euo pipefail
issue_web_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$issue_web_dir"
issue_pw_version="$(node -p 'require("./node_modules/@playwright/test/package.json").version')"
[[ "$issue_pw_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "不支持的 Playwright 版本: $issue_pw_version" >&2; exit 1; }
issue_image="mcr.microsoft.com/playwright:v${issue_pw_version}-noble"
mkdir -p test-results/issue-workspace
[[ -w test-results/issue-workspace ]] || { echo "测试报告目录不可写" >&2; exit 1; }
docker info >/dev/null
if ! docker image inspect "$issue_image" >/dev/null 2>&1; then
  docker pull "$issue_image"
fi
docker image inspect "$issue_image" | node -e '
  let input = "";
  process.stdin.on("data", chunk => input += chunk).on("end", () => {
    const [image] = JSON.parse(input);
    console.log(JSON.stringify({ image: image.Id, entrypoint: image.Config.Entrypoint ?? null, cmd: image.Config.Cmd }));
  });
'
# 显式替换 ENTRYPOINT，直接执行项目 CLI；不继承镜像服务入口。
exec docker run --rm --init --shm-size=1g \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$issue_web_dir,target=$issue_web_dir" \
  --workdir "$issue_web_dir" \
  --entrypoint node \
  "$issue_image" node_modules/@playwright/test/cli.js test \
  --config playwright.issue.config.ts "$@"
