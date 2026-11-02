# 洛谷测试数据自动生成系统

在 Windows 本地运行的 Python + C++17 工具。配置 DeepSeek 或其他 OpenAI 兼容 API 后，把 `problem.md` 放入 `inbox`，程序会分析题面、生成代码、构造测试点、计算标准答案、验证并输出压缩包。也支持单次命令行和批处理。

当前完成的是**传统题、唯一输出**的自动生成流程。交互题、提交答案题、需要 SPJ 的多解题和浮点容差题会明确拒绝；不把这些题当作普通题猜测生成。模型分析和有限对拍都不是形式化证明。输入缺少关键约束、歧义审查不通过、验证失败时停止打包，并写入错误报告。

## Windows 开始使用

1. 安装 Python 3.11+ 并加入 PATH。
2. 安装 [MSYS2](https://www.msys2.org/)，在 **UCRT64** 终端运行：

   ```bash
   pacman -S --needed mingw-w64-ucrt-x86_64-gcc
   ```

3. 将 `C:\msys64\ucrt64\bin` 加入用户 PATH，重新打开 PowerShell，在项目目录运行：

   ```powershell
   .\setup.ps1
   ```

`setup.ps1` 创建虚拟环境、保留已有设置文件、检查编译器。核心程序仅使用 Python 标准库；无需联网安装 Python 依赖。安装可选上传功能使用 `setup.ps1 -WithUpload`，它安装 Playwright 和 Chromium。若本机策略禁止运行 PowerShell 脚本，可以按下面的等价命令手工初始化，无需修改机器全局策略：

```powershell
python -m venv .venv
Copy-Item settings.example.toml settings.toml
.\.venv\Scripts\python.exe main.py --doctor
```

安装后可直接双击 `run.cmd`。如果没有配置密钥环境变量，会在本地终端隐藏输入 API Key，仅本次进程使用，退出后清除，不写文件。已配置密钥时不会重复询问。本地模型接口不需要密钥时直接启动。启动器先实际编译并运行 C++17 检查程序，再监听 `inbox`。

先运行无需密钥的完整示例：

```powershell
.\.venv\Scripts\python.exe main.py examples\problem.md --points 20 --seed 42 --output outputs\example
```

Linux 也支持，安装 g++ 后运行 `python3 main.py ...`。`--cxx` 可指定 g++ 的完整路径；不支持 MSVC `cl.exe` 的命令行。Windows 下强制 Python 子进程 UTF-8，标准解 CRLF 输出转为 LF，文件按字节写入；进程资源通过 Windows Job Object 限制。Windows/Linux 自动测试工作流位于 `.github/workflows/test.yml`，尚未在本次云环境中执行 Windows 实机测试。

## 配置 DeepSeek 或其他国内 API

复制 `settings.example.toml` 为 `settings.toml`，后者已被 Git 忽略。示例：

```toml
[model]
base_url = "https://api.deepseek.com"
model = "deepseek-chat"
review_model = "deepseek-chat"

[generation]
points = 20
seed = 42
stress = 100
cxx = "g++"
```

其他供应商使用其文档中的 OpenAI 兼容**基础地址**（可能带 `/v1` 或 `/compatible-mode/v1`）；本程序追加 `/chat/completions`。模型需要支持 JSON Object 模式、非流式响应和 8192 输出 token。每个源码文件分开请求，减少整套代码被截断的风险。`review_model` 可选用同一接口上的其他模型，以减少共同错误。命令行参数优先于设置文件；没有设置文件时也支持 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_REVIEW_MODEL`。

主程序从本机 `LLM_API_KEY` 或 `DEEPSEEK_API_KEY` 环境变量读取密钥；`run.cmd` 的隐藏输入也只注入当前进程环境。不接受设置文件中的密钥，不写入报告和缓存。也可以在 PowerShell 安全输入：

```powershell
$secureKey = Read-Host "API Key" -AsSecureString
$env:LLM_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
.\run.cmd
```

`run.cmd` 持续监听 `inbox`。把题面存成 `inbox\problem.md`，或 `inbox\题目A\problem.md`；文件连续两轮稳定后处理。每个内容版本使用独立输出目录。修改题面会触发新的任务，失败会保留报告；不变的失败文件不会反复调用收费 API，修正后或重启监听再试。使用相同 PowerShell 启动 `run.cmd` 才能继承本次输入的环境变量。关闭后可 `Remove-Item Env:LLM_API_KEY`。

监听输出目录同时包含**题面哈希与运行配置哈希**。修改点数、种子、模型、策略或升级程序后，重启监听会创建新任务目录；原配置重新启动则继续恢复旧任务。

本地自检和可选的真实 API 自检：

```powershell
python main.py --doctor --diagnostic-report doctor.json
python main.py --test-api --diagnostic-report api-check.json
```

`--doctor` 真正编译并运行使用 C++17 功能的程序，同时检查含中文的路径；仅显示密钥是否存在，不调用模型。`--test-api` 由你明确发起，对生成模型和复核模型发送最小 JSON 请求，可能产生少量费用；响应不满足契约会返回失败。诊断报告不含密钥，URL中误填的凭据或查询参数也不会输出。默认启动、`--doctor` 和离线示例均不会执行真实 API 自检。

题面、约束和源码会发送到你配置的服务。默认一次任务有9次模型请求：题意分析、题意复核、6个源码文件（含2个错误解）、代码复核。调用数随错误解数量变化。429/部分5xx最多重试3次；连接错误和截断输出停止。真实供应商调用会产生费用，本项目的 API 协议测试使用本机模拟服务，不宣称已完成真实 DeepSeek 联调。

`--provider auto` 对原样示例使用离线实现，对其他题面调用 API。离线实现按题面哈希精确匹配，不根据题目关键词猜解法。`--provider api` 强制使用模型；`--provider builtin` 只接受原样示例。

## 单次执行、批处理、恢复

```powershell
python main.py problem.md --points 20 --seed 42 --output outputs\p
python main.py problems --output outputs\batch
python main.py inbox --watch --output outputs\inbox
```

批处理递归找 `problem.md`；如果没有则处理顶层 `*.md`。每题独立状态，某题失败不阻断其他题，最终返回非零退出码。不要在题面目录混入 README。目录监听只处理 `problem.md`。

```powershell
python main.py problem.md --until analyze --output outputs\p
python main.py problem.md --until generate --output outputs\p --resume
python main.py problem.md --until verify --output outputs\p --resume
python main.py problem.md --until package --output outputs\p --resume
```

阶段为 `analyze → build → generate → verify → package`，不能跳过前置条件。恢复检查题面、参数、策略、流水线代码以及每个已完成产物的哈希。另有模型请求缓存、逐测试点检查点、逐次小数据对拍进度，进程中断后不必重做已成功的工作。任何阶段检查失败都撤销 ZIP；`partial`、`failed` 和 `uploaded_unverified` 不等于通过。

修改参数、题面或程序版本后需要新输出目录；手动修改已验证源码或数据后，不能通过恢复重新认可这些改动。未知非空目录不会覆盖。相同模型参数重新请求仍不保证生成同样代码；精确复现需保留本次源码与缓存，用 `--resume` 复用。同一源码、种子、工具链和 zlib 下 ZIP 可字节复现。

## 题面与分析契约

Markdown 和 KaTeX 原文完整保留，数学表达式交由模型解读，不运行 KaTeX 或题面嵌入代码。至少需要一组样例，推荐写法：

````markdown
## 输入样例
```input
5
-2 3 -1 4 -5
```
## 输出样例
```output
6
```
````

也支持输入/输出样例标题下的无语言围栏，以及波浪号围栏。不闭合或无法配对的样例会报错。

题意分析先生成结构化规格：输入输出、完整约束及逐字证据、非空/重复/符号条件、算法、证明、复杂度、时空限制、分组、覆盖计划、格式例外和常见错误解计划。随后独立模型请求审查歧义；复核通过后才生成源码。

标准解、暴力和验证器各自通过独立请求生成，**暴力和验证器请求不接收标准解实现**。最后再做整体代码复核。格式例外必须引用题面原文；没有例外时严格检查 ASCII 可见字符、单空格、无空行/首尾空格和 LF 结尾。缺少证据或任何复核拒绝会停止，但模型仍可能共同遗漏题意，所以报告明确保留该风险。

## 数据强度与验证

- 根据实际语义设计最小、最大、随机、相等、单调、极值和特殊结构；不适用的类型不机械加入。
- 正式数据携带覆盖标签，由独立验证器检查构造属性，全部测试点的标签必须覆盖规格中的计划。
- 每个输入检查完整题目约束；有 Subtask 时再检查组约束。
- 标准解和暴力用 C++17 编译；每组输入运行标准解生成 `.out`。
- 运行题面样例、默认100次小规模边界/随机暴力对拍；`--stress` 最少20。
- 对输出采用洛谷官方说明的比较：忽略行末空白及文末换行，其余全文结构保留。`1 2` 不等价于两行 `1`、`2`。
- 重复运行生成器及标准解检查确定性。
- 生成至少2个有具体错误原因的错误解并编译；**每个错误解都必须被正式数据击败**，否则停止打包。错误解不是另一套正确性证明。
- 最后检查 ZIP 成对、命名、目录、大小、CRC 和内容一致性。

C++ 有时间/内存/输出限制；Windows 使用 Job Object，Linux 使用 rlimit 和进程组。生成代码仍以当前用户权限运行，这些限制不是文件或网络沙箱。建议在独立本地账户或 Windows Sandbox 中处理不可信模型代码。子进程移除名称含 KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL 的环境变量，但仍不能替代隔离。

## 洛谷配置及官方规范

已核查 [洛谷官方帮助中心源码](https://github.com/luogu-dev/docs)，提交 `9cd55c03faf4ef0d66f8dfa9558d819821c30902`，核查日期2026-11-02（UTC+8）。来源与文档哈希保存在 `luogu_data/policy.json`。

- [测试点配置](https://help.luogu.com.cn/manual/luogu/problem/testcase-config)：以输入/输出文件名为键。
- [上传要求](https://help.luogu.com.cn/manual/luogu/problem)：ZIP 根目录，无子文件夹/无关文件，文件名只有一段连续数字，压缩后不超过50M、解压后不超过100M。
- [数据格式](https://help.luogu.com.cn/manual/luogu/problem/testcase-format)：ASCII与空白规范、LF换行、默认全文比较。
- [计分脚本](https://help.luogu.com.cn/manual/luogu/problem/scoring-script)：复杂分组依赖需额外计分脚本。

程序按十进制50,000,000/100,000,000字节保守解释文档中的 M；最多100点、单文件50,000,000字节是本工具资源上限。`--policy` 只允许收紧这些限制，不能放宽官方门槛或伪造来源。

```yaml
1.in:
  timeLimit: 1000
  memoryLimit: 262144
  score: 5
```

时间单位毫秒，内存单位KB。原题无分组时不写 `subtaskId`，默认20个独立点各5分；其他点数整数均分并分配余分，总计100。原题有分组时写组编号和分值，生成 `scoring.json` 包含组内全对得分脚本和支持依赖的整题计分脚本。**洛谷 config.yml 不能承载这些脚本，必须在页面中应用后核验**；浏览器模块会要求提供相应脚本编辑框及保存按钮映射。脚本不作为无关文件放入测试数据 ZIP。

时空限制以题面为准；题面未指定时明确标注默认1000ms/256MiB。本机运行给时间容差，不能由本机耗时直接证明洛谷时限合理。报告记录实际耗时，内存仅实施上限，未声称采集峰值内存。

检查官方规则是否变化：

```powershell
python main.py --check-rules
```

此命令在线获取官方仓库当前文档，与已核实哈希比较并输出 `rules-check.json`；文档变化就返回失败，不能由程序盲目把新规则标记为支持。网络失败也不算已核查。

## 输出文件

| 文件 | 内容 |
|---|---|
| `standard.cpp` / `brute.cpp` | 标准解和独立暴力 |
| `generator.py` / `validator.py` | 生成器、题目约束及格式/覆盖验证器 |
| `wrong_*.cpp` | 用于验证数据强度的错误解 |
| `spec.json` / `review.json` | 规格、证据、复核结果 |
| `cases/*.in` / `cases/*.out` | 正式输入和答案 |
| `config.yml` / `scoring.json` | 洛谷配置及必要的页面计分脚本 |
| `testcases.zip` | 验证完成后生成的根目录数据包 |
| `report.md` | 测试点规模、构造、耗时、验证和风险 |
| `state.json` / `model_cache/` | 恢复状态及分请求缓存 |
| `validation.json` | 样例、对拍、覆盖、错误解击败记录 |
| `failure/` | 失败反例及已获得的实际/期望输出 |
| `upload-receipt.json` | 可选上传的状态、页面配置及验收结果 |

## 可选本机浏览器上传

默认不访问账户或上传。用户自行在程序打开的可见浏览器中登录；程序不要求提供密码、Cookie或令牌，不导入/导出登录态。

```powershell
python -m pip install -e ".[upload]"
python -m playwright install chromium
python main.py problem.md --output outputs\p --resume --upload-preview https://www.luogu.com.cn/problem/U123456
```

最后一条只显示本地预览。真正上传：

```powershell
python main.py problem.md --output outputs\p --resume --upload https://www.luogu.com.cn/problem/U123456 --upload-adapter upload-adapter.local.json
```

流程：人工登录 → 读取并展示远端标题与题面、本地题面、文件及配置 → 用户在同一标签页打开该题数据管理页 → 输入 `UPLOAD 题号 ZIP哈希前12位` → 程序才选择文件并上传 → 等待新成功提示 → 保存必要的计分脚本 → 刷新页面逐行核对时空限制、分数和分组 → 下载服务端数据包并逐文件比对字节。

任一步骤不明确、目标页面改变、文件被改动、页面配置不一致或下载内容不一致都会停止。上传后的失败记为 `uploaded_unverified`，不会自动重传，也不会声称已经验收。即使用户明确启动上传命令，仍要求本次具体文件的确认。

`upload-adapter.example.json` 是**本地测试页的字段映射示例，不是已验证的洛谷生产页面选择器**。需依据你登录后的实际管理页面更新为 `upload-adapter.local.json`。目前已在真实 Chromium + 本机模拟站点测试完整流程；本轮没有登录洛谷账号，也没有在真实题目上上传。页面选择器不匹配时程序拒绝继续，不用猜测按钮或跳过验证。

## 开发与测试

```powershell
python -m unittest discover -s tests -v
python main.py examples\problem.md --points 20 --seed 42 --stress 100 --output outputs\validation
```

完整浏览器测试需安装可选依赖和 Chromium；未安装时测试会明确标记跳过。当前云环境的执行证据见 `VALIDATION.md`。仓库提供 Windows/Linux CI 配置，但工作流文件存在不等于Windows已通过。

内置例题为最大非空连续子段和，标准解 Kadane、暴力枚举左右端点；错误解覆盖允许空子段和32位答案溢出。通用题目的分析实际由用户配置的模型负责，示例成功不等于任意题目都已证明正确。
