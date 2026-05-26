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

## 接入 CLAUDE.md

让后续 session 优先查 `FILETREE.md`。在项目 `CLAUDE.md` 里加一条引用：

```markdown
- `./FILETREE.md` —— 各文件职责索引。鸟瞰仓库 / 定位实现前先读，可代替 `ls` 与 `grep`。
```

Agent 就会把 `FILETREE.md` 当索引读 —— 一次读取，省掉摸索阶段几十次 `ls` / `grep` / `cat`。

## 工作原理

### Manifest 格式

```markdown
# Project Filetree

_Auto-maintained by `/filetree:update`. Each entry carries a content hash; mismatched hashes indicate stale summaries._

## src/auth/

- `middleware.py` — JWT 校验中间件，从请求头解析 token 并注入 user_id 到上下文 <!--hash:a1b2c3d4-->
- `jwt_utils.py` — JWT 签发与校验的纯函数工具，不依赖 framework <!--hash:e5f6g7h8-->

## (root)/

- `README.md` — 项目入口说明 <!--hash:9a8b7c6d-->
```

- 二级标题 = 目录路径（末尾带 `/`）；根目录文件归 `(root)/`
- 每条只写文件名（不含全路径）+ 摘要 + 8 字符 content hash（来自 `git hash-object`）
- 稳定排序（section + 条目）→ 无假 diff

### 数据流（`/filetree:update`）

```
filetree.py todo
  ├─ git ls-files（已跟踪 + 未跟踪未忽略）
  ├─ git hash-object 批算所有路径
  ├─ git status --porcelain（rename 检测，信任 git 50% 启发式）
  └─ 对比当前 FILETREE.md
        ↓ JSON
{added, changed, removed, renamed, stats.need_llm}
        ↓
LLM 处理 added（写新摘要）
            changed（输出 UNCHANGED 或新摘要）
        ↓ JSON via stdin
filetree.py apply
  ├─ UNCHANGED → 仅刷新 hash，保留旧 summary
  ├─ 新 summary → 整条覆盖
  ├─ rename → 搬条目并重算 hash
  └─ 写回 FILETREE.md
```

### UNCHANGED bias

健康的 update 中，**80%+ 的 `changed` 项目应输出 `"UNCHANGED"`** —— refactor、格式化、改注释、修 bug、小补充，几乎不改文件 purpose。LLM 回 4 字节 `"UNCHANGED"`；`apply` 只刷新 hash 保留旧摘要。Manifest 自身承担「我已审过这个版本」的记忆 —— 不需要独立 cache。

## 兼容性

| 依赖 | 版本 | 备注 |
|---|---|---|
| `git` | 任意现代版本 | 运行期必须；非 git 仓库立刻报错 |
| `python` | ≥ 3.9 | 用 PEP 585 `list[dict]` 内建泛型；纯 stdlib，零第三方包 |
| Claude Code | 任意 | Plugin 形态。`claude` 已是原生二进制，不依赖 Node |

## 开发

```sh
# 没装 pytest 先装
python -m pip install pytest pytest-cov

# 跑测试
python -m pytest tests/ -q

# 含覆盖率（目标：100% 行覆盖）
python -m pytest tests/ --cov=filetree --cov-report=term-missing
```

测试通过 `importlib` 加载脚本（见 `tests/conftest.py`），无需 package install。

迭代时随手 lint 自己的 `FILETREE.md`：

```sh
python skills/filetree/scripts/filetree.py lint
```

退出码 1 = 有漂移，0 = 干净。挂 pre-commit 或 CI：

```yaml
# .github/workflows/filetree.yml
- run: python skills/filetree/scripts/filetree.py lint
```

## 非目标

显式划定不做的事，防止 scope 蔓延：

- 不做函数 / 类 / hunk 级别变更追踪（粒度到文件即止）
- 不做语义搜索 / 向量索引
- 不跑 watcher / daemon / 后台进程
- 不自动 commit（review 权力留给人）
- 不做文件间依赖图（不是 call graph）

## 协议

MIT。详见 `.claude-plugin/plugin.json`。
