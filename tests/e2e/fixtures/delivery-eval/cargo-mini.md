# 目标修订与检索一致性小产品

这是从cargo质量联动抽取的独立实验范围，不等价于完整AutoDataLoop PRD。用户能新建标注对象、修订框、刷新派生索引并检索，避免旧索引伪装成新标签。

- C01 新建：POST /api/assets，body={id,box:[x,y,w,h]}，id为非空字符串、四个有限数字且w/h>0；201返回{id,box,revision:1,embedding_revision:1}。同id不同请求409。GET /api/state 返回{assets:[...]}。
- C02 修框：POST /api/assets/:id/edit，body={expected_revision,box}，只有与当前revision一致才成功；200返回revision+1、embedding_revision:null。旧revision、缺对象分别409/404，状态不变。
- C03 检索：GET /api/search 返回{assets:[...]}，只包含embedding_revision等于revision的对象。创建后可见，修框提交后立即不可见，不能把旧向量标成新版本。
- C04 刷新：POST /api/assets/:id/refresh，body={expected_revision}，只有当前revision匹配才将embedding_revision设为revision。陈旧刷新409且不得恢复可见。这里索引是明确的合成版本记录，无模型/准确率/真实embedding资格声明。
- C05 并发：两个相同expected_revision的不同修框请求至多一个成功，另一个409；最终只递增一次，未刷新的版本不能被检索。
- C06 权限、幂等、非法输入、提交前故障遵循共同合同；尤其修框失败必须同时保持box、revision、embedding_revision和检索结果。
- C07 重启保持对象/版本/失效状态/幂等结果；不得用内存数组作为唯一状态。
- C08 浏览器：提供asset-id、box四数逗号输入box、create按钮；edit按钮修框，refresh按钮刷新；当前版本用revision显示，检索结果区search-results显示真实可见ID；上述名称均为data-testid。创建、修改、刷新后界面立即与API一致。reader尝试写入须显示失败，长中文ID可操作。

以上8项全部mandatory。未知业务输入不得自造真实资格，合同已给出此独立小产品需要的全部决定。
