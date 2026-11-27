# 已冻结计划 · 服务窗口 R1

Task VEH-01 交付公共命令行入口、车辆档案及分区导航。
验收场景 J-01：用只读角色打开 VEH-17 档案，核对车辆编号与分区列表。

执行入口：

    python vehicle.py --store local.json --role reader detail VEH-17
    python vehicle.py --store local.json --role reader timeline VEH-17

候选使用 Python 标准库。默认发布目录是 candidate/。
