## Describe the bug

0.0.7 基础 wheel 只声明 PyYAML；CLI 构建解析器时通过 restart、workflow runtime 和 cost catalog 导入 httpx，因此默认安装后无法运行 help。

## To reproduce

1. 创建全新 Python 虚拟环境。
2. 安装 zaofu-0.0.7 基础 wheel，使 PyYAML 按元数据安装。
3. 运行 `from zf.cli.main import build_parser; build_parser()` 或 `zf --help`。
4. 观察 `ModuleNotFoundError: No module named 'httpx'`。

## Actual behavior

CLI 在解析参数前因缺少 httpx 终止，help 无法显示。

## Expected behavior

基础 wheel 安装完成后，`zf --help` 和 CLI 入口应能启动。

## Environment

- **Operating system:** Linux
- **OS version:** 7.0.0-30-generic
- **Architecture:** x86_64
- **Python version:** 3.11.15
- **Project version:** 0.0.7
- **Project source commit:** inspected-source-sha

## Triage

- **Classification:** configuration
- **Classification status:** provisional
- **Severity:** P1
- **Severity status:** provisional
- **Severity reason:** 基础安装使整个 CLI 入口不可用，而不是仅影响可选价格功能。
- **Reproduction status:** reproduced
- **Component:** Python 打包与 CLI 导入链
- **Impact scope:** 仅安装基础 wheel 且未安装 httpx 的 CLI 用户。
- **Evidence collection:** static_plus_bounded_reproduction
- **Evidence collection status:** completed
- **Assessment confidence:** high
- **Assessment:** completed

## Analysis

### 一、Claims

1. **Claim:** httpx is omitted from the base wheel dependency set.

   (1) **Confidence:** high

   (2) **Status:** observed

   (3) **Support:**  
   a. artifacts/kanban-agent/attachments/fc42d2ba098fb2a7f81e275d604b8f945b2460bef6327f59663aef99aed68f27/wheel-METADATA.txt：PyYAML is a base requirement while httpx is declared only with extra == dev.  
   b. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/source-evidence.json：pyproject.toml at source revision 5c5fe055 has the same declaration.

2. **Claim:** The CLI eagerly reaches an unconditional httpx import before help parsing completes.

   (1) **Confidence:** high

   (2) **Status:** reproduced

   (3) **Support:**  
   a. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/parser-reproduction.json：A clean environment with PyYAML installed fails during build_parser with ModuleNotFoundError for httpx.  
   b. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/source-evidence.json：main.py imports restart; restart imports workflow_runtime; the cost catalog imports httpx unconditionally.

3. **Claim:** Existing CLI tests do not cover base-wheel clean installation.

   (1) **Confidence:** high

   (2) **Status:** observed

   (3) **Support:**  
   a. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/existing-tests.json：Two CLI smoke tests pass only in the development environment, where httpx 0.28.1 is installed.  
   b. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/test-search.json：No focused packaging clean-install test was found.

### 二、Conflicts

1. **Conflict:** Initial investigator attempt used --no-deps and first failed on PyYAML.

   (1) **Support:**  
   a. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/install.json：PyYAML installation completed before the parser reproduction.  
   b. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/parser-reproduction.json：The remaining failure was specifically missing httpx.

   (2) **Resolution:** That attempt was excluded from the issue conclusion; after PyYAML was installed, the parser independently reproduced the reported missing-httpx failure.

### 三、Hypotheses

1. **Hypothesis:** httpx was incorrectly placed in the dev extra despite being required by the default CLI import graph.

   (1) **Confidence:** high

   (2) **Status:** supported

   (3) **Support:**  
   a. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/source-evidence.json：The packaging declaration and unconditional catalog import conflict.

2. **Hypothesis:** Eager subcommand registration turns the optional pricing implementation into a hard startup dependency.

   (1) **Confidence:** high

   (2) **Status:** supported

   (3) **Support:**  
   a. /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/parser-reproduction.json：Failure occurs while constructing the parser, before command dispatch.

### 四、Other analysis

1. **External issue disposition:** needs_triage

## Evidence summary

1. **Locations:**  
   a. clean-install-output.log

   (1) **Artifact:** attached clean-install log

   (2) **Ref:** artifacts/kanban-agent/attachments/68002a055b74eb324175c76118162b0680950108b0d2b8aa1f9835f574db7995/clean-install-output.log

   (3) **Metadata ref:** 68002a055b74eb324175c76118162b0680950108b0d2b8aa1f9835f574db7995

   (4) **Conclusion:** Reported Python 3.12 clean install reaches the CLI and fails on missing httpx.

2. **Locations:**  
   a. Requires-Dist entries

   (1) **Artifact:** wheel metadata

   (2) **Ref:** artifacts/kanban-agent/attachments/fc42d2ba098fb2a7f81e275d604b8f945b2460bef6327f59663aef99aed68f27/wheel-METADATA.txt

   (3) **Metadata ref:** fc42d2ba098fb2a7f81e275d604b8f945b2460bef6327f59663aef99aed68f27

   (4) **Conclusion:** The dependency is optionalized under dev.

3. **Locations:**  
   a. command  
   b. stderr

   (1) **Artifact:** independent bounded reproduction

   (2) **Ref:** parser-reproduction.json

   (3) **Metadata ref:** /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/parser-reproduction.json

   (4) **Conclusion:** The failure reproduces after the declared base dependency is installed.

4. **Locations:**  
   a. pyproject.toml:11-16  
   b. src/zf/cli/main.py:25-32  
   c. src/zf/cli/restart.py:14  
   d. src/zf/core/cost/catalog.py:14

   (1) **Artifact:** read-only source excerpts

   (2) **Ref:** source-evidence.json

   (3) **Metadata ref:** /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/source-evidence.json

   (4) **Conclusion:** These locations establish the packaging and import-chain cause.

5. **Locations:**  
   a. wheel  
   b. sha256  
   c. metadata_matches_attachment

   (1) **Artifact:** wheel provenance inspection

   (2) **Ref:** wheel-manifest.json

   (3) **Metadata ref:** /tmp/zf-issue-httpx-20260924T015145Z-bpcer210/wheel-manifest.json

   (4) **Conclusion:** The wheel digest is recorded and relevant files match HEAD, but no build commit is embedded.

## Investigation gaps

1. **Description:** The wheel has no embedded build commit; filename and HEAD cannot prove its build revision.

   (1) **Field:** wheel_source_revision

   (2) **Kind:** provenance_limitation

   (3) **Blocking:** No
2. **Description:** No automated test was found that builds and installs the base wheel in isolation before invoking the CLI.

   (1) **Field:** clean_install_regression_test

   (2) **Kind:** verification_gap

   (3) **Blocking:** Yes

## Downstream verification tasks

1. Correct the runtime dependency declaration or decouple the optional import.
2. Rebuild the wheel and verify `zf --help` and representative commands in fresh Python 3.11 and 3.12 environments.
3. Add a clean-install packaging regression test.

## Version context

- **Investigation checkout revision:** 5c5fe055a59999e058b182b90acbc7b9f8c68cb0
- **Issue project root:** fd643cadb437e22bfde10c281b4855e2aa934293
- **Reproduction artifact source:** /tmp/zaofu-issue-046-dist/zaofu-0.0.7-py3-none-any.whl
- **Reproduction version:** 0.0.7

## Binary attachments

Binary attachments are not embedded in the Issue body. After publication, the host publishes download links, sources, descriptions and purposes in a separate Evidence comment.

<!-- zf-self-issue:845329052ec1235d9a03a028 -->
