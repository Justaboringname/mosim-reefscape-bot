# MoSim REEFSCAPE bot（1323 Madtown）

[English](README.md)

在 [MoSimulator](https://store.steampowered.com/app/4398690/) 里自动打完一整局 **REEFSCAPE（FRC 2025）** 单车比赛的脚本 bot，驾驶游戏内置的 **1323 Madtown**。没有用强化学习，也没有用录像示范：每个技能都是从游戏机制推出来的，再用游戏内的 A/B 批次调参。分数 = 蓝方 − 红方，场上没有对手。

## 一览

| | |
|---|---|
| 当前均分 | 约 **194–195**（每个配置 40–80 局） |
| 单局最高 | **205** |
| ≥200 的局 | 约 **四分之一** |
| 典型「满分局」 | auto 5 个 L4 + reef 放满 36 个 coral + 6 个 reef algae 进 net + deep climb = 193，再加 1–3 个地上 algae 或 trough coral |
| 批量速度 | 4 个实例 × 20 局约 35 分钟 |

分数进展（游戏内批次均值，括号里是局数）：

| 版本 | 改动 | 均分 |
|---|---|---|
| v35 | station 接 coral，用游戏自带 AutoAlign 放置 | 168.8 (10) |
| v41 | teleop 循环参数搜索（CMA-ES） | 179.5 (40) |
| v44–v46 | 自己的放置控制器（「到位即对准、到位即放」）、ground intake、net 修复 | 187.9 (30) |
| v48 | auto 第 2 个 coral 用 ground intake，终局捡地上 algae | 188.7 (30) |
| v49 | auto 预装 coral 正面放远侧第 4 面（teleop 里最费时的位置） | 189.1 (20) |
| v50–v52 | reef 六角避撞、去 net 路上提前转向、终局按时钟决策、coral handoff 卡死脱困 | 193.7 (20) |
| v56 | 送件段朝向控制 + 放置速度门槛 0.25 m/s：满 reef 提前约 1.5–2 s | 194.2 (80) |
| v57 | 更积极地捡地上 algae，捡球限时保住 climb | 195.2 (40) |
| v58 | 终局打 L1 trough（无可测增益：194.1 vs 不打 194.3） | 194.1 (40) |

N=20 时均分的标准误约 1 分，单批差距小于 2 分基本是噪声。小的提速改动改用每个循环分段耗时（`py/reefbot/cycle_report.py`）来判断。

## 打法

**Auto（15 s）：** 预装 coral 正面放远侧的 reef 第 4 面（开局就朝着 reef），第 2 个用 ground intake 捡，3 个立着的 staged coral 用 end effector 竖着吸，时间够再用 ground intake 捡第 6 个。通常 auto 放 5 个 L4。

**Teleop coral 循环（约 2.9 s）：**
- *取件：* ground intake 捡代价最小的地上 coral（station 会不断往地上掉 coral）。先沿 coral 轴线在 0.75 m 外对准，再以约 1.2 m/s 碰上去（`GROUND_V_TOUCH`；`GROUND_FAST=0` 是更慢的「刚好停在 coral 前再推 5 cm」）。场内对不准、或接近路线上有别的 coral 的就跳过；车底压了 coral 先甩掉。
- *送件：* 绕 reef 的 A* 路径（reef 视为一个圆加六个角上的小圆，所以能贴着面走又不蹭角），边走边转好朝向，coral 一进 end effector 就按 level，最后前馈刹停在 branch 位姿。距离 <2.5 cm、角度 <1.5°、速度 <0.25 m/s 且电梯稳定时放手。L2/L3 背放，远侧面的 L4 正放。
- *顺序：* 同层级就近贪心（先 L4），某面的 L3 需要时顺手摘掉该面的 algae。

**Algae：** 只从正面摘（背面摘的 algae 卡在爪子深处，会往后飞），去 net 路上提前抬臂，最后一米斜着进（直着进容易卡在 rim 上），进 cage 区前先对齐 z，球坐低、车停稳才出手。

**终局：** reef 放满后，时间够就捡地上 algae 送 net，不够就打 L1 trough。捡地上 algae 和送 net 都受 climb 预留时间约束；trough 只在时间估计够的时候才开始。Deep climb 带滑脱检测和重试。

大部分打法来自用户自己的操作经验：*到位时朝向和电梯都已就绪，不花额外时间对准；贴着墙和 reef 走；边走边转；auto 第 2 个从地上捡，staged coral 用 end effector 竖着吸。*

## 没用的尝试

- **Processor：** 蓝方 +6，但 processor 会把 algae 射进红方 net（红方 +4），净 +2，不如进 net 的 +4。
- **参数搜索（CMA-ES）：** 三轮里有两轮小幅提升（+1.5 到 +2），远不如上面那些结构性改动。
- **远侧面放 staged coral**（丢了 auto 第 5 个）、**直着进 net**、**锁步 bridge 跑真实时钟**（会退化到约 1 fps）、**放置速度门槛放到 0.35 m/s**（1% 失败，每次补放约 3 s）。
- **终局 trough：** 能用（只能正面、半径 ≤1.40 m，演练约 90% 进），但均分没变。
- 离稳定 200 的差距：第二个地上 algae 通常还差约 3 s。

## Harness：怎么接进游戏

- `tools/Injector`（Mono.Cecil）在本地已安装的某个 mod DLL 的构造函数里插一个调用，加载 `harness/MoSimRL`。我们用的是 China Modpack 的 `Alphabots.dll`；换别的 mod 需要改 `tools/install-hook.sh` 里的路径和类型名。用 `tools/restore-hook.sh` 撤销。
- `ReefBridge.cs` 是锁步 TCP JSON bridge（默认每次决策 10 个物理步 = 45 ms）。状态：车的位姿和速度、机构 setpoint / 电梯 / coral 和 algae 状态、场上每个 coral 和 algae（位置、是否被持有、是否得分、朝向、实例 id）、reef 位图、trough 计数、双方分数明细。动作：场地坐标速度、角速度、按键点按和按住（`ReefButtons.cs` 注入机器人自己的输入）。
- `py/reefbot/gamectl.py` 通过 Steam 启动器拉起实例。MoSim 26.4.2 起启动器会丢掉命令行参数，所以实例用环境变量配置，以小窗口在后台运行，结束后还原用户自己的显示设置。
- **不上传排行榜。** bot 实例里 harness 每帧都把游戏的 `wasCheated` 置真，所以 bot 局永远不会提交。正常的人类对局不受影响。

## 目录

| 路径 | 内容 |
|---|---|
| `py/reefbot/` | `bot.py`（状态、驾驶控制律、障碍物）、`nav.py`（绕 reef 路由、网格 A*、前瞻跟踪）、`geom.py`（场地位姿）、`client.py`（bridge 客户端、按键）、`skills.py`（intake、放置、net、trough）、`matchbot.py`（比赛逻辑、auto 路线、终局、climb）、`planner.py`（实验性的按每秒得分规划，默认关闭）、`gamectl.py`（启动器）、`demo.py`（可见的单局）、演练（`drill_*.py`）、标定脚本（`calib*.py`）、报表（`batch_report.py`、`cycle_report.py`）、参数搜索（`search_*.py`） |
| `harness/MoSimRL/` | 游戏内 C#：`Probe.cs`（入口、不上传保护）、`ReefBridge.cs`、`ReefButtons.cs`、`Compat.cs`、`WindowPin.cs` |
| `tools/` | `Injector/`（Cecil hook）、`install-hook.sh`、`restore-hook.sh` |
| `data/field.json` | 从游戏场景导出的场地几何（reef 各面、station、cage） |

## 运行

需要 macOS、Steam 版 MoSimulator 26.5.x、一个本地已安装的 mod 用来挂 hook、.NET 9 SDK（或更新）、Python 3.11。如果希望某个程序运行时 bot 不启动，设置 `MOSIMRL_BUSY_PROCS`（一个 `pgrep` 匹配串）。

```bash
# 游戏运行时把它的 Managed 目录拷出来（磁盘上的 app 只是个启动壳）：
#   .../MoSimulator.app/Contents/Resources/Data/Managed  ->  ./managed
dotnet build harness/MoSimRL -c Release          # -> harness/bin/MoSimRL.dll
tools/install-hook.sh                             # 先退出游戏；用 tools/restore-hook.sh 撤销
mkdir -p run && touch run/ENABLE
cd py && python -m reefbot.matchbot 47601 20 mytag   # 跑 20 局，日志在 <仓库>/runs/reef/
python -m reefbot.demo 47601 2560x1440               # 实时跑一局可见的比赛
```

## 状态与注意事项

- 默认配置是 v58（`AUTO_VARIANT=g2_hyb4`）。大部分行为开关是环境变量（见 `bot.py`、`skills.py`、`matchbot.py` 里的 `os.environ.get`）；调过的连续参数可以用 `PARAMS_JSON` 覆盖。多数默认值旁边记着选它的那次 A/B。
- 已知 bridge 小问题：`trough` 状态列表里两个 trough 的顺序在不同局之间可能对调（用总和）。trough 约 90% 的数字是按总和从演练日志重新统计的。
- 为了发布，游戏内入口裁剪成只有 Reefscape bridge，运行目录和 Python 路径改成了仓库相对路径。harness 能编译通过，但这些发布改动没有在游戏里重新跑过。

## 声明

- MoSimulator 归其开发者所有。本项目与他们无关，不包含任何游戏代码或美术资源。
- 个人研究项目，MIT 许可证。
- 代码和文档由 Claude（Anthropic）在用户指导下编写。
