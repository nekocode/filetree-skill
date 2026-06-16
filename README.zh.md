# filetree

[English](README.md)

Claude Code plugin：维护 `FILETREE.md` —— 一行一文件，自带内容 hash 检测过期。让 LLM 在动手前用几百 token 就能掌握仓库地形。

## 为什么

每次进入项目，Claude 都要重新摸索：`ls`、`grep`、打开、读、再读。摸索昂贵，且结果不可跨 session 复用。

| 痛点 | filetree 的解法 |
|-----|-----|
| LLM 每次会话重新摸索结构 | 固化为 `FILETREE.md`，纳入 git，团队共享 |
| 描述静默过期 | 每条带内容 hash，hash 错位即暴露漂移 |
| 小重构就重写所有描述费 token | `UNCHANGED` bias —— 文件 purpose 没变只刷 hash（约 100x 节省）|
| 工具引入 sqlite / daemon / watcher | 单 markdown 文件，变更检测全部委托给 git，零后台进程 |

## 安装

**通过 marketplace：**

```
/plugin marketplace add nekocode/filetree-skill
/plugin install filetree
```

**本地开发 / dog-fooding**（无需安装）：

```sh
cd /path/to/filetree-skill
claude --plugin-dir .
```

改完 `commands/` 或 `SKILL.md` 后，session 内跑 `/reload-plugins` 热重载。

## 命令

| 命令 | 功能 |
|---------|---------|
| `/filetree:init` | 从零生成 `FILETREE.md`。已存在时要求确认覆盖 |
| `/filetree:update` | 同步 `FILETREE.md` 与仓库当前状态（added / changed / removed / renamed）|
| `/filetree:lint` | 只读漂移检查，有漂移退出码非 0，CI 友好。**不调用 LLM** |

所有命令都不会自动 commit `FILETREE.md`，diff 留给你审。

## 接入 CLAUDE.md / AGENTS.md

`/filetree:init` 首次执行时会顺手做 —— 扫 repo 根的 `CLAUDE.md` / `AGENTS.md`，已经有 `## FILETREE.md` 子章节的跳过，剩下的提议一个独立的 `## FILETREE.md` 子章节（按文件语言改写），每次落盘前你都先确认。

注意事项：

- `CLAUDE.md` 与 `AGENTS.md` 都不存在时，plugin 不会替你创建 —— 你自己挑要不要建。建完重跑 `/filetree:init` 即可 wire。
- wire 只在 init 时做一次。后续才加 `CLAUDE.md` / `AGENTS.md`，重跑 `/filetree:init`（已存在的 `FILETREE.md` 会问要不要覆盖）或者手动接。

想手动接入，往 `CLAUDE.md` 里加这样一段：

`````markdown
## FILETREE.md

`FILETREE.md` 按目录分组，逐文件一行点明职责。`ls`/`grep`/`find` 之前先读它按用途定位代码 —— 把「搜仓库」变成「查索引」。自动维护，勿手改。

```
## (root)/

- `manage.py`：Django CLI 入口

## src/auth/

- `jwt.py`：JWT 中间件；解析 token，注入 user_id
- `session.py`：基于 Redis 的服务端会话存储
```
`````

Agent 就会把 `FILETREE.md` 当索引读 —— 一次读取，省掉摸索阶段几十次 `ls` / `grep` / `cat`。

## 项目配置（`.filetree.json`）

可选。仓库根放一个 `.filetree.json`，纳入 git 团队共享。缺省 → 走默认。

```json
{
  "manifest_path": "docs/FILETREE.md",
  "exclude": ["migrations/", "**/*.gen.ts", "/build"],
  "include": ["*.svg"],
  "language": "zh",
  "commit_guard": true
}
```

| key | 作用 | 默认 |
|---|---|---|
| `manifest_path` | manifest 落盘路径（仓库内相对路径） | `FILETREE.md` |
| `exclude` | gitignore 风格模式，把已跟踪文件移出 manifest | `[]` |
| `include` | gitignore 风格模式，索引默认会跳过的文件（如 `*.svg`） | `[]` |
| `language` | pin summary 语言（如 `"zh"`），不走自动探测 | `null` |
| `commit_guard` | 拦截 Claude 发起的 `git commit`，自动更新 `FILETREE.md` | `false` |

`exclude` / `include` 支持完整 gitignore 语法（`/build`、`**`、`!keep.gen.ts`、目录尾斜杠）。配置非法立即报错。

## Manifest 格式

```markdown
# Project Filetree

_Auto-maintained by `/filetree:update`. Content hashes live in the sidecar `FILETREE.hash.json`; do not edit it by hand._

## (root)/

- `README.md`: 项目入口说明

## src/auth/

- `middleware.py`: JWT 校验中间件，从请求头解析 token 并注入 user_id 到上下文
- `jwt_utils.py`: JWT 签发与校验的纯函数工具，不依赖 framework
```

- 二级标题 `## dir/` = 完整目录路径；根目录文件归 `## (root)/`
- 文件行 `` - `name`: 摘要 `` —— 纯文本，无行内噪声；agent 直接从所属章节标题读出文件位置
- 章节字典序、章节内文件字典序 → 无假 diff
- 内容 hash 存到旁路文件 `FILETREE.hash.json`（`{path: hash}`），manifest 因此无逐行 hex 噪声。旧版行内 `<!--hash:-->` manifest 会在下次 update 时自动迁移。

## 兼容性

| 依赖 | 版本 | 备注 |
|---|---|---|
| `git` | 任意现代版本 | 运行期必须；非 git 仓库立刻报错 |
| `python3` | ≥ 3.9 | 用 PEP 585 `list[dict]` 内建泛型；纯 stdlib，零第三方包。插件统一调用 `python3`（现代 macOS / 全新 Linux 无 `python` 命令） |
| Claude Code | 任意 | Plugin 形态。`claude` 已是原生二进制，不依赖 Node |

## 开发

```sh
# 没装 pytest 先装
python3 -m pip install pytest pytest-cov

# 跑测试
python3 -m pytest tests/ -q

# 含覆盖率（目标：100% 行覆盖）
python3 -m pytest tests/ --cov=filetree --cov-report=term-missing
```

测试通过 `importlib` 加载脚本（见 `tests/conftest.py`），无需 package install。

迭代时随手 lint 自己的 `FILETREE.md`：

```sh
python3 skills/filetree/scripts/filetree.py lint
```

退出码 1 = 有漂移，0 = 干净。挂 pre-commit 或 CI：

```yaml
# .github/workflows/filetree.yml
- run: python3 skills/filetree/scripts/filetree.py lint
```

## 协议

MIT。详见 `.claude-plugin/plugin.json`。
