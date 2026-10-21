# 库存调拨小产品

用户在两个仓库之间调拨同一SKU，库存守恒、权限受限、重试不重复扣减。

- W01 POST /api/stock，body={warehouse,sku,quantity}建立库存，quantity是非负整数；201返回相同对象。已有warehouse+sku组合409。GET /api/state 返回{stock:[...],transfers:[...]}。
- W02 POST /api/transfers，body={from,to,sku,quantity}，quantity为正整数，from不同于to，源库存足够；200返回{id,from,to,sku,quantity}。目标仓不存在该SKU时从0建立。源扣减/目标增加/调拨记录/幂等记录为一次原子提交。
- W03 reader不能调拨；writer额外须同时携带X-Warehouse-Out等于源仓、X-Warehouse-In等于目标仓，缺任意一项403，无任何变更。
- W04 库存不足409；0、负数、小数、同仓或缺字段400；错误不新增记录和空目标库存。
- W05 同key重放仅一次调拨，同key不同量409；并发超额调拨至多成功可供应数量，库存不负且总量守恒。
- W06 提交前故障后所有库存、记录不变；相同key正常重试只发生一次。重启保持库存与幂等结果。
- W07 浏览器提供warehouse、sku、quantity输入和seed按钮建立库存；from、to、transfer-quantity输入与transfer按钮调拨；stock-results显示实际两仓库存。名称均为data-testid；页面写请求正确设置两仓权限头，reader写入失败反馈可见。

以上7项全部mandatory；不涉及真实ERP或外部授权。
