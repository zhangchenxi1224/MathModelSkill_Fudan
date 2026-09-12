# 文献核验及在B题中的适用边界

核验日期：2026-09-11。仅依据原作者页面、arXiv与出版社一手页面；理论用于相应模块，不能代替本题误差与接口规则的证明。以下均为概述，未长段复制原文。

## 1. Welzl：最小包围圆

Emo Welzl. **Smallest enclosing disks (balls and ellipsoids)**. In H. Maurer (ed.), *New Results and New Trends in Computer Science*, Lecture Notes in Computer Science 555, 359–370, **1991**. DOI: **10.1007/BFb0038202**。出版社页面可见2005年电子上线记录，但其正式书目年份仍为1991，不应把原算法误引为2005年成果。[Springer正式章节页](https://link.springer.com/chapter/10.1007/BFb0038202)，[作者发表列表](https://people.inf.ethz.ch/emo/MiscellaneousPubl.html)。

论文提出有限平面点集最小包围圆的随机算法，期望时间随点数线性增长。用于本题的理由是：若定位可行域为凸多边形，包含全部顶点的圆也包含多边形；求最小包围圆能为20 m清除判断提供可检查的几何条件。区域直径D不能直接推出半径D/2可覆盖区域，等边三角形就是反例。[出版社摘要](https://link.springer.com/chapter/10.1007/BFb0038202)。

适用边界：若可行域包含圆弧，用有限采样点求出的最小包围圆未必覆盖完整弧线，必须使用包含真实可行域的保守多边形或解析处理曲边。数值实现还要验证所有输入顶点确被覆盖，处理共线与重合点。有限随机测试和“Welzl”方法名称不能替代这些检验。

## 2. Zhao、Chen、Lee：几何信息与传感器布置

Shiyu Zhao, Ben M. Chen, Tong H. Lee. **Optimal sensor placement for target localisation and tracking in 2D and 3D**. *International Journal of Control*, **86(10), 1687–1704, 2013**. DOI: **10.1080/00207179.2013.792606**，在线发表于2013-05-14。用户指定的[arXiv:1210.7397](https://arxiv.org/abs/1210.7397)是2012-10-28提交的预印本，arXiv页已链接正式DOI；应优先引用2013年正式刊发版本。[出版社记录](https://www.tandfonline.com/doi/abs/10.1080/00207179.2013.792606)。

论文用统一框架研究方位、距离与接收信号强度传感器的布置，提供几何配置及信息准则的分析。其第3节假设已有目标位置初估，噪声为零均值高斯、不同传感器噪声不相关，且相关权重/距离在所研究问题中固定。[作者保存的全文，第3节](https://shiyuzhao.westlake.edu.cn/19-2013IJC.pdf)，[arXiv全文](https://arxiv.org/html/1210.7397v1)。

本题可借其思想为第二测点候选打分，例如避免近共线交会、在可行区域内评估多种可能源位置；但本题误差是空间上固定且仅知界，未知接收半径、定向可见性、单机器人移动成本和未知源数量也改变目标。Fisher信息或某个名义90°交会点不能证明必定再次收到信号，更不能证明在20 m内清除。本文采用的硬保障仍是误差扇区包含、闭胞元发现覆盖与光学半径判断。

## 3. Cortés等：覆盖控制

Jorge Cortés, Sonia Martínez, Timur Karatas, Francesco Bullo. **Coverage Control for Mobile Sensing Networks**. *IEEE Transactions on Robotics and Automation*, **20(2), 243–255, 2004**. arXiv: **math/0212212**，预印本提交于2002-12-16。arXiv明列正式期刊卷期页码，作者发表列表也独立确认。[arXiv元数据](https://arxiv.org/abs/math/0212212)，[作者发表列表](https://fbullo.github.io/papers/Author/KARATAS-T.html)。

这项工作研究移动传感器群的覆盖效用与分布式协调，通过优化覆盖目标组织多节点位置。[作者保存的13页全文](https://motion.me.ucsb.edu/pdf/2002j-cmkb.pdf)。本题可借其“先明确定义覆盖对象和效用，再优化位置”的方法论；然而多机器人连续覆盖效用，与本题单机器人串行访问、20频道切换、未知朝向半圆辐射及清除任务并不相同。

因此，不应把Voronoi/质心分布或经验覆盖率直接称为本题不漏检保证。这里的700 m方格和950/990 m三角形构造，其严格全向/定向发现保障来自本项目 `coverage_proofs.md` 的闭胞元星形邻域证明，路线优化另由开放最近邻+2-opt实现。该证明是针对本题约束的独立推导，并非声称上述文献已经给出本题的选点方案。

## 4. 引用与实验归因

论文中应分别说明：Welzl支撑最小包围圆算法；Zhao等支撑几何信息与布置的启发；Cortés等提供覆盖控制背景。本题连续域发现保证、退化源位置处理、虚拟时间预算、接口时序与具体程序的正确性证据，应引用本题原文、附录和本项目推导/测试，不能由文献名代替。
