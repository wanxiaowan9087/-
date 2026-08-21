# 阿里云短信验证码注册调研

> 目的：为“手机号验证码注册 + 用户协议/隐私协议同意”功能提供实施前的官方事实与工程约束。本文不是接口契约，也不授权生产实现。
>
> 调研日期：2026-08-21（Asia/Shanghai）  
> 来源范围：阿里云官方文档。

## 1. 官方接入事实

### 1.1 发送接口

- 短信发送使用 `Dysmsapi` 的 `SendSms`，API 版本为 `2017-05-25`，中国站服务接入点为 `dysmsapi.aliyuncs.com`。该 API 是 RPC 风格，支持 `POST` / `GET`，官方建议使用 `POST`。
- 业务调用必须由后端 SDK / Adapter 发起。关键参数为 `PhoneNumbers`、`SignName`、`TemplateCode`；`TemplateParam` 是 JSON 字符串；`OutId` 可用于关联本平台的发送记录。
- 成功以响应 `Code=OK` 判断，并应保存供应商的 `BizId`（回执 ID）和 `RequestId`，供回执查询与故障排查使用。
- 国内短信的超时建议设为不少于 1 秒。供应商明确指出国内短信不具备幂等能力，调用超时后应先检查回执，再决定是否重试，避免重复短信和重复计费。

来源：[SendSms - 发送短信](https://help.aliyun.com/zh/sms/developer-reference/api-dysmsapi-2017-05-25-sendsms)。

### 1.2 资质、签名与模板

- `SignName` 和 `TemplateCode` 必须是同一阿里云账号下已经审核通过的资源；发送端不能临时生成，也不能由浏览器传入。
- 国内短信签名前需要按实名发送要求提交资质；验证码模板也需经审核。实际排期必须把资质、签名、模板审核放在开发前置条件中。
- 注册验证码应使用一条固定、审核通过的模板。变量仅用于验证码等审核允许的内容，禁止把用户自由输入内容拼入模板变量。

来源：[创建短信签名](https://help.aliyun.com/zh/sms/user-guide/create-a-signature)、[创建短信模板](https://help.aliyun.com/zh/sms/user-guide/create-a-template)、[短信服务开通与审核说明](https://help.aliyun.com/zh/sms/getting-started/sms-service-quick-start)。

### 1.3 供应商频控与计费边界

阿里云对发往中国内地的验证码，默认限制为：同一签名、同一手机号最多 1 条/分钟、5 条/小时、10 条/天；同一手机号跨短信发送方最多 40 条/天。短信提交成功即计入供应商频控，即使运营商回执失败也会计入。`SendSms` 的单用户 QPS 限制为 5000/秒。

工程侧不能把供应商频控当作唯一风控。平台注册发送应再施加更严格的手机号、IP、设备/会话与账号维度限流，并在供应商调用前拦截。

来源：[短信发送规则](https://help.aliyun.com/zh/sms/user-guide/message-rules)、[SendSms - 发送短信](https://help.aliyun.com/zh/sms/developer-reference/api-dysmsapi-2017-05-25-sendsms)。

### 1.4 鉴权与凭证安全

- 不使用阿里云主账号 AccessKey。官方建议采用最小权限 RAM 用户或 RAM 角色；部署在 ECS 等云环境时优先使用实例 RAM 角色或 STS 临时凭证。
- 如在本地或过渡环境使用 AccessKey，仅由进程环境变量 `ALIBABA_CLOUD_ACCESS_KEY_ID` 与 `ALIBABA_CLOUD_ACCESS_KEY_SECRET` 注入。不得写入前端包、仓库、`.env.example`、日志、异常响应或截图。
- 权限示例中的 `AliyunDysmsFullAccess` 仅适合快速验证。正式环境应自定义最小策略，仅允许本服务所需的 `dysms:SendSms`，并将凭证轮换、泄露应急与审计纳入部署说明。

来源：[管理访问凭据](https://help.aliyun.com/zh/sdk/developer-reference/v2-manage-access-credentials)、[快速调用短信 API](https://help.aliyun.com/zh/sms/developer-reference/how-to-use-api-quickly)、[SendSms 授权信息](https://help.aliyun.com/zh/sms/developer-reference/api-dysmsapi-2017-05-25-sendsms)。

## 2. 错误与可观测性

常见供应商错误应在 Adapter 层转换为项目的稳定业务错误码，不把阿里云 `Message` 原样暴露给浏览器：

| 阿里云错误码示例 | 对外语义 | 是否建议重试 |
| --- | --- | --- |
| `isv.BUSINESS_LIMIT_CONTROL`、`isv.DAY_LIMIT_CONTROL`、`isv.MONTH_LIMIT_CONTROL` | 请求过于频繁或触发平台限制 | 否，按冷却期提示 |
| `isv.MOBILE_NUMBER_ILLEGAL` | 手机号格式或号段不合法 | 否 |
| `isv.SMS_TEMPLATE_ILLEGAL`、`isv.TEMPLATE_MISSING_PARAMETERS`、`isv.SMS_SIGNATURE_ILLEGAL`、`isv.SIGN_STATE_ILLEGAL` | 服务端短信配置不可用 | 否；告警并由运维处理 |
| `isp.RAM_PERMISSION_DENY` | 服务身份权限不足 | 否；告警 |
| `isv.AMOUNT_NOT_ENOUGH`、`isv.OUT_OF_SERVICE`、`isv.PRODUCT_UN_SUBSCRIPT` | 服务不可用或余额/订阅异常 | 否；告警 |

日志可记录脱敏手机号、平台 `request_id`、本地发送记录 ID、阿里云 `RequestId`、`BizId`、供应商 `Code`、耗时和限流命中维度。严禁记录验证码明文、完整手机号、`TemplateParam` 中的敏感内容或任何 AccessKey。

来源：[短信服务 API 错误码](https://help.aliyun.com/zh/sms/developer-reference/api-error-codes)。

## 3. 普通短信方案的项目内实现边界

以下建议仅适用于选择普通短信 `Dysmsapi/SendSms` 的情形，需经后续设计和 OpenAPI 审批后实施。当前控制台已有号码认证服务赠送签名/模板时，应优先采用第 6 节的号码认证方案，不能把本节的“本地生成并保存验证码”与阿里云动态验证码方案混用：

1. **前端最小输入**：只提交规范化手机号和验证码。签名、模板、供应商凭证、`OutId` 生成规则都只在后端配置和 Adapter 层存在。
2. **验证码生命周期**：后端使用密码学安全随机数生成 6 位验证码；只存 HMAC 或哈希，不存明文。保存 `phone_hash`、验证码摘要、用途 `register`、过期时间、发送次数、失败次数和一次性消费状态。推荐 Redis TTL 5 分钟；若 Redis 不可用，明确拒绝发送而不是降级为明文数据库存储。
3. **反滥用**：在调用阿里云前执行手机号冷却期（至少 60 秒）、手机号小时/日配额、IP 和设备/会话限流，以及同一 IP 多手机号异常检测。验证错误达到阈值后锁定该验证码挑战；所有限流响应避免泄露该手机号是否已注册。
4. **幂等与恢复**：平台创建 `sms_send_attempt` 后以其 UUID 作为 `OutId`；发送请求使用本地幂等键/分布式锁，防止并发重复提交。遇网络超时，先按本地 attempt 和供应商回执状态核验，禁止无条件重试 `SendSms`。
5. **注册事务**：仅在验证码已通过、手机号未被占用、用户协议和隐私协议均已显式同意并记录文档版本/时间/IP 后，创建用户、绑定手机号并将验证码原子地标记为已消费。失败时不应创建半成品账号。
6. **隐私最小化**：手机号是个人信息。数据库只保存业务所需手机号及其索引/展示掩码，日志用哈希或掩码；协议同意记录与用户数据使用目的、保留与删除策略一致。

## 4. 实施前待确认项

- 短信资质、签名和“注册验证码”模板是否已在阿里云控制台审核通过；未通过时只能做 Mock / 开发环境 Fake Provider。
- 生产部署的凭证提供方式：建议服务器 RAM 角色；本地开发使用单独的低权限 RAM 凭证。
- 手机号是否作为登录名，还是仅作为验证过的账号属性；此选择会影响登录、找回密码、换绑和唯一索引设计。
- 用户协议与隐私协议的首个版本、展示 URL/文本、版本号与生效时间；注册时应记录同意的准确版本，而不能只存一个布尔值。

## 5. 资料索引

全部链接访问日期：2026-08-21。

1. [SendSms - 发送短信](https://help.aliyun.com/zh/sms/developer-reference/api-dysmsapi-2017-05-25-sendsms)
2. [短信发送规则](https://help.aliyun.com/zh/sms/user-guide/message-rules)
3. [短信服务 API 错误码](https://help.aliyun.com/zh/sms/developer-reference/api-error-codes)
4. [快速调用短信 API](https://help.aliyun.com/zh/sms/developer-reference/how-to-use-api-quickly)
5. [管理访问凭据](https://help.aliyun.com/zh/sdk/developer-reference/v2-manage-access-credentials)
6. [创建短信签名](https://help.aliyun.com/zh/sms/user-guide/create-a-signature)
7. [创建短信模板](https://help.aliyun.com/zh/sms/user-guide/create-a-template)
8. [短信服务开通与审核说明](https://help.aliyun.com/zh/sms/getting-started/sms-service-quick-start)

## 6. 号码认证服务补充：应选哪条验证码链路

用户控制台展示的是**号码认证服务（PNVS）**提供的“短信认证服务”，其中有阿里云赠送签名和模板。它与普通短信服务 `Dysmsapi` 不是同一个产品，也不能混用 API。

| 对比项 | 普通短信服务 | 号码认证服务的短信认证 |
| --- | --- | --- |
| 产品/API | `Dysmsapi` `SendSms`，`2017-05-25` | `Dypnsapi` `SendSmsVerifyCode` + 核验 API，`2017-05-25` |
| 短信签名/模板 | 需使用本账号审核通过的普通短信签名与模板 | 官方推荐使用号码认证控制台赠送的签名与模板；赠送签名必须搭配赠送模板 |
| 验证码的生成与校验 | 应用生成并校验；应用负责哈希、TTL、一次性消费 | 可让阿里云生成和核验。`TemplateParam` 使用占位符且传入生成规则时，官方注明验证码由 API 动态生成、阿里云接口可完成校验；若传入固定验证码，则阿里云不能完成校验 |
| 服务端实现复杂度 | 较高，但全程由当前 FastAPI + Redis 统一掌控 | 状态可由供应商维护，但必须按该服务的核验 API/令牌语义接入，仍需本地限流、审计和注册事务 |
| 本项目适配性 | 适合已经具备普通短信审核签名/模板的项目 | 当前截图已有赠送签名/模板，且普通个人资质无法完成普通签名报备时，应优先采用此服务 |

### 6.1 号码认证 API 的事实

- 发送 API 为 `Dypnsapi` 的 `SendSmsVerifyCode`；核验有 `CheckSmsVerifyCode`，官方说明其返回核验是否成功。其 RAM 权限点分别为 `dypns:SendSmsVerifyCode` 和 `dypns:CheckSmsVerifyCode`。
- `SendSmsVerifyCode` 支持手机号、方案名、`SignName`、`TemplateCode`、`TemplateParam`、外部流水号、验证码长度和有效期等参数。官方文档标明验证码有效期默认 300 秒；验证码生成类型和动态模板变量关系到是否可由阿里云进行核验。
- `CheckSmsVerifyCode` 需要发送链路一致的方案名（若发送时填写）、国家码、手机号、`OutId` 和 `VerifyCode`。因此平台必须保存本地发送 attempt 与传给供应商的 `OutId`，而不是只有手机号加验证码。此路径不需要客户端号码认证 SDK；另一些令牌型核验接口与其不同，不能混为一谈。
- 该服务只支持国内号码，默认国家码为 `86`。本项目的初始注册范围应明确为中国内地手机号。
- Python SDK 包为 [`alibabacloud-dypnsapi20170525`](https://pypi.org/project/alibabacloud-dypnsapi20170525/)。SDK 应使用产品端点解析机制；常用公网端点为 `dypnsapi.aliyuncs.com`，部署前须在目标环境通过 SDK/官方 OpenAPI Explorer 验证。

来源：[SendSmsVerifyCode - 发送短信验证码](https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-sendsmsverifycode)、[CheckSmsVerifyCode - 核验验证码](https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-checksmsverifycode)、[Python SDK 包](https://pypi.org/project/alibabacloud-dypnsapi20170525/)。

### 6.2 计费和个人资质结论

- **号码认证服务可以按量付费。**官方定义为先使用后付费，也支持套餐包；短信认证按运营商回执状态计费，提交成功但运营商回执失败不计费，验证码核验服务免费。当前官方价目中 API 版短信认证月用量不超过 1000 次的公开阶梯价为 0.06 元/次，具体价格以控制台账单和当期产品页面为准。
- **普通短信服务也可以按量付费。**官方按短信发送条数、短信模板类型实施梯度计费；套餐包耗尽后默认转为按量付费。
- 但“可按量付费”不等于“个人身份可自由发送普通短信”。普通国内短信的签名报备和模板审核仍受资质及实名发送规则约束。官方开通说明指出，无法提供企业资质信息的个人认证用户，推荐使用号码认证产品的短信认证服务。因此，当前拥有赠送签名/模板的场景应选择 `Dypnsapi`，不应为了普通 `Dysmsapi` 另行尝试绕过签名资质要求。

来源：[号码认证服务产品计费](https://help.aliyun.com/zh/pnvs/product-overview/product-pricing)、[短信服务计费概述](https://help.aliyun.com/zh/sms/product-overview/billing-overview)、[短信服务开通与审核说明](https://help.aliyun.com/zh/sms/getting-started/sms-service-quick-start)。

### 6.3 最小权限与凭证泄露处置

号码认证服务的最小权限应仅授予本项目实际调用的 Action：

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dypns:SendSmsVerifyCode",
        "dypns:CheckSmsVerifyCode"
      ],
      "Resource": "*"
    }
  ]
}
```

不要授权 `dypns:*`、`*` 或使用主账号 AccessKey。若最终选用不同的官方核验 API，只授予对应的单个 `dypns:*` Action，并更新本段与部署配置。

**严重安全事项：截图中出现了 AccessKey ID 和 AccessKey Secret，应将这对 AccessKey 视为已经泄露。**仅从截图中抹去或不把它写入代码都不足够。应立即在 RAM 控制台禁用或删除该 Key，创建替代的最小权限 RAM Key 或给服务器配置 RAM Role，再检查 ActionTrail、账单与异常 API 调用。新凭证仅通过服务器密钥管理或环境变量注入。开发和部署工作应在替换完成后继续。

来源：[AccessKey 泄露处理](https://help.aliyun.com/zh/ram/user-guide/what-to-do-if-an-accesskey-pair-is-leaked)、[管理访问凭据](https://help.aliyun.com/zh/sdk/developer-reference/v2-manage-access-credentials)。

### 6.4 当前项目的推荐落地边界

基于当前可见的号码认证服务赠送签名/模板，推荐将注册验证码 Provider 固定为 **阿里云号码认证 `Dypnsapi` 的 `SendSmsVerifyCode + CheckSmsVerifyCode`**：

1. 后端固定配置赠送签名、模板 Code、方案名、验证码长度和有效期；浏览器永远不能提交这些参数。
2. 发送时让阿里云动态生成验证码，平台只保存 `phone_hash`、本地 attempt ID、`OutId`、供应商 `BizId` / `RequestId`、过期时间、发送/核验状态及脱敏审计信息，**不保存验证码明文、哈希或 HMAC**。
3. 注册提交时，后端调用 `CheckSmsVerifyCode`；仅在供应商核验成功、手机号未占用且两份协议已同意时，才创建用户。无论成功或失败，都按本地 attempt 状态限制重复核验。
4. 本地仍必须执行发送冷却、手机号/IP/会话限流、反枚举、用户协议与隐私政策版本留痕及发送审计。这些职责不会因供应商代管验证码而消失。
5. 在实施前使用新建的最小权限 RAM 凭据，在阿里云 OpenAPI Explorer 完成一次“赠送签名 + 赠送模板 + 动态验证码 + Check 核验”的人工验证；验证成功后再冻结 OpenAPI 及 Provider 配置字段。
