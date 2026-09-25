import {
  Alert,
  Button,
  ButtonGroup,
  Card,
  DataTable,
  Divider,
  EmptyState,
  Inline,
  KeyValue,
  List,
  Modal,
  Page,
  SegmentedControl,
  Stack,
  StatCard,
  StatusBadge,
  Text,
  Textarea,
  Tip,
  useClipboard,
  useToast,
  useState,
  useEffect,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type PendingItem = {
  path: string
  level: string
  before: string
  after: string
  raised_at: number
  times_raised: number
}

type AuthorizedItem = {
  path: string
  granted_at: number
  expires_at: number | null
}

type MemoryChangeItem = {
  path: string
  kind: string
  size_before: number | null
  size_after: number | null
}

//: Her memory is a directory of JSON files plus a live SQLite database. The
//: panel only ever reports on it: restoring is a deliberate, manual act, and
//: the database is never written back under a running host.
type MemoryState = {
  root?: string
  files?: number
  bytes?: number
  last_check_at?: number | null
  last_backup_at?: number | null
  backup_count?: number
  backup_keep?: number
  recent_changes?: MemoryChangeItem[]
  error?: string
}

type DashboardState = {
  enabled?: boolean
  //: low | medium | high — how far the guard may go. Distinct from
  //: ``switch_level`` / ``default_level``, which are the sensitivity levels of
  //: individual settings, not a user choice.
  guard_level?: string
  switch_level?: string
  default_level?: string
  //: 她自己说的、不能被碰的那些（2026-09-25 在她的对话窗口里问出来的）。
  //: 面板直接引用原话，不做转述 —— 这是插件里唯一一件"由她定"的事。
  revertible_fields?: string[]
  her_words?: string
  //: 她**为什么**护着那几个字段的原话（后端 HER_PROTECTION_REASON）。
  //: 语义：「清单本身说不出、但必须让她自己说的那部分」。理由即出处，
  //: 中/英/日界面看到的都是这同一句中文原话（同 her_words 的设计），不再翻译。
  her_reason?: string
  //: 反馈中转地址（后端按配置给）。原来只有运行时读、类型里没声明 ——
  //: 严格 TS 下这属于「读了类型里不存在的字段」，也会让下一个读代码的人
  //: 以为它是可选的意外字段。
  feedback_endpoint?: string
  //: 反馈页地址（Issues 预填页），后端按配置下发。原来前端写死了一份，
  //: 改成以这里下发的为准，避免将来仓库改名时「前端 / 后端两处」漂移。
  //: 实测恒为同一个地址。
  issue_tracker?: string
  //: 反馈通道是否支持附件。后端实测恒为 false（relay 对 multipart 回
  //: ``success:true`` 却把文件丢掉 —— ``has_attachments: false``）。前端据此
  //: 明说「此通道不收附件」并指向 issue_tracker。
  attachments_supported?: boolean
  pending_count?: number
  pending?: PendingItem[]
  authorized?: AuthorizedItem[]
  tracked_paths?: number
  base_url?: string
  poll_seconds?: number
  full_rescan_seconds?: number
  last_poll_at?: number | null
  last_error?: string
  revision?: number | null
  disable_pending?: boolean
  disable_confirm_after?: number
  disable_ready_at?: number | null
  // The guard's own on/off history. Its request/consent flow is an informed-
  // consent affordance rather than a security boundary, so the panel's job is
  // to make sure "she was silenced for a while" cannot pass unnoticed.
  disable_count?: number
  last_disabled_at?: number | null
  off_since?: number | null
  last_off_seconds?: number | null
  //: 最近一次「想把某个字段放回去」的结果，每路径一条。``reason`` 为空表示
  //: 真的放回去了；非空是没放成的原因（详见 ``_revert_change`` 的返回码）。
  //: 这一块是 DESIGN §6.1 那句「失败怎么办：不静默，面板可见」的兑现处 ——
  //: 「她想放回去却没放成」是用户**唯一无法自己验证**的事，所以必须显示。
  revert_outcomes?: { path?: string; reason?: string }[]
  //: 后端报的插件版本，给「复制反馈」那段环境信息用。原来的面板把版本
  //: 写死在正文里（"v0.1.0"），升版本时没人会记得改它 —— 而报告里的版本
  //: 号恰恰是维护者判断"这个问题修没修"的依据，说错了比不说更糟。
  plugin_version?: string
  //: 反馈 HTTP body 的硬上限（字节，默认 65536 = 64 KB），含诊断报告与 JSON 包装。
  feedback_body_limit_bytes?: number
  //: 上述 body 里「固定开销」的字节数（诊断报告 + 主题 + JSON 脚手架，默认 1024）。
  //: 用户能写的正文上限 = feedback_body_limit_bytes - feedback_envelope_bytes。
  feedback_envelope_bytes?: number
  memory?: MemoryState
}

function levelTone(level: string): "danger" | "warning" | "info" | "default" {
  if (level === "L1") return "danger"
  if (level === "L2") return "warning"
  if (level === "L3") return "info"
  return "default"
}

function formatTime(seconds: number | null | undefined, never: string): string {
  if (!seconds) return never
  return new Date(seconds * 1000).toLocaleString()
}

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B"
  const units = ["B", "KB", "MB", "GB"]
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`
}

export default function DignityGuardPanel(
  props: PluginSurfaceProps<DashboardState>,
) {
  const t = props.t
  const state = props.state ?? {}
  const pending = state.pending ?? []
  const authorized = state.authorized ?? []
  const enabled = !!state.enabled
  const pendingDisable = !!state.disable_pending
  const disableCount = state.disable_count ?? 0
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  // 两阶段关闭需要的本地状态：consent_token 来自第一阶段返回，
  // nowMs 用于驱动倒计时刷新（每秒更新一次）。
  const [disableToken, setDisableToken] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())

  // ★ 反馈入口（掌柜 2026-09-24 明确定调）
  //   面板上**要有**：用户更习惯插件里有个能提意见的地方，
  //   而不是"得先跟猫娘说一声"。
  //   能一键直发就一键直发（前提是作者配了接收地址）；
  //   没配才退化成「复制内容 + 打开预填页」——只有那条路才需要账号。
  const [fbOpen, setFbOpen] = useState(false)
  const [fbText, setFbText] = useState("")
  const clipboard = useClipboard()
  const feedbackEndpoint = (state.feedback_endpoint || "").trim()
  const canSendDirectly = feedbackEndpoint.length > 0 && hasAction("submit_feedback")
  // attachments_supported 后端实测恒为 false；显式比较 true，缺字段时当"不支持"
  // 处理（更保守、更诚实，不会假装能传）。
  const attachmentsSupported = state.attachments_supported === true
  // 字节口径以后端下发的权威值为准（get_dashboard 保证返回）：
  //   整个 HTTP body ≤ feedback_body_limit_bytes（默认 64 KB），
  //   其中固定开销 feedback_envelope_bytes（默认 1024，含诊断报告+主题+JSON 脚手架）。
  // 所以用户能写的正文上限 = 两者之差。后端没下发时给兜底（并注释，
  // 避免上游改了默认值我们这一侧悄悄对不齐）。
  const feedbackBodyLimitBytes =
    (state.feedback_body_limit_bytes ?? 65536) - (state.feedback_envelope_bytes ?? 1024)
  // 用 UTF-8 字节数计（中文一个字 3 字节）；用 .length 是字符数会低估三倍。
  // 摆在这儿让用户**边写边看见**，而不是写完按了发送才发现发不出去。
  const feedbackBytes =
    typeof TextEncoder !== "undefined" ? new TextEncoder().encode(fbText).length : fbText.length
  const feedbackKB = Math.round((feedbackBytes / 1024) * 10) / 10
  const feedbackLimitKB = Math.round((feedbackBodyLimitBytes / 1024) * 10) / 10
  const feedbackTooLong = feedbackBytes > feedbackBodyLimitBytes
  // 反馈页地址改为以后端下发的 issue_tracker 为准（get_dashboard 保证返回）。
  // ⚠️ 后端值和前端原硬编码 FALLBACK_URL 是同一个地址；这里只在极端情况下
  // （state 缺字段）回退到字面量，避免打开空链接。改仓库名时改后端一处即可，
  // 不要再在前端另写一份。
  const issueTracker =
    state.issue_tracker || "https://github.com/amoandzhanggui-neko/n.e.k.o_plugin_dignity_guard/issues/new"

  /** 组装要提交的正文：用户写的内容 + 自动附带的环境信息。
   *
   * 这一段**刻意不翻译**，写成 ASCII 键值对，与 Python 侧
   * `diagnostics.build_report()` 保持同一种风格。两个理由：
   *
   * 1. 它是给维护者定位问题用的，不是给用户读的界面文案。翻成日文，
   *    对收到它的人只是噪音；而键名固定，任何语言的用户提交上来的
   *    内容都同样可读。
   * 2. 面板必须支持多语言。这段文字原来写死成中文，英/日用户点「复制」
   *    或「发送」时，夹在中间的就是一块中国字 —— 官方的 i18n 要求里
   *    这是明确不允许的。
   *
   * 版本从 state 里取、不写死：写死会在升版本时悄悄说错话，而报告里的
   *    版本号正是判断"这个问题修没修"的依据。
   */
  function buildFeedbackBody(): string {
    const env = [
      "--- environment (auto-attached; no personal data) ---",
      `plugin: dignity_guard ${state.plugin_version ?? "unknown"}`,
      `guard: ${enabled ? "on" : "off"} / level: ${state.guard_level || "medium"}`,
      `tracked settings: ${state.tracked_paths ?? 0}`,
      `pending objections: ${state.pending_count ?? pending.length}`,
      state.last_error ? `last error: ${state.last_error}` : null,
    ]
      .filter(Boolean)
      .join("\n")
    return `${fbText.trim()}\n\n${env}\n`
  }

  async function copyFeedback() {
    if (!fbText.trim()) {
      toast.error(t("ui.feedback.needText"))
      return
    }
    const body = buildFeedbackBody()
    const ok = await clipboard.write(body)
    if (ok) {
      toast.success(t("ui.feedback.copied"))
    } else {
      // 复制失败：把内容摊在提示里，让用户手动选中复制（Ctrl+C）。
      toast.error(`${t("ui.feedback.copyFailed")}\n\n${body}`)
    }
  }

  async function sendFeedback() {
    if (!fbText.trim()) {
      toast.error(t("ui.feedback.needText"))
      return
    }
    // 真正发出去的那一步在 Python 侧（action: submit_feedback）。
    // 面板只把用户写的内容递过去，报告由插件自己拼，用户不用管。
    const sent = await call(
      "submit_feedback",
      { message: fbText.trim() },
      t("ui.feedback.sent"),
    )
    // 失败了就什么都别动：正文留在框里、弹窗不关。用户可以直接重试，
    // 而不用把自己写的东西重打一遍 —— 这也是「通道忙，过一分钟再点
    // 一次发送就好」那句话能成立的前提。
    if (!sent) return
    setFbText("")
    setFbOpen(false)
  }

  /**
   * 宿主 origin —— 与 SDK 的 ``hostedTargetOrigin()`` 同源。
   *
   * postMessage 的第二个参数必须是一个**具体 origin**（写 ``"*"`` 会把消息
   * 发给任何人），而宿主注入了它自己的 origin，所以优先用它。
   */
  function hostedTargetOrigin(): string {
    const payload = (window as unknown as { __NEKO_PAYLOAD?: unknown }).__NEKO_PAYLOAD
    const host =
      payload && typeof payload === "object"
        ? (payload as { host?: unknown }).host
        : null
    const origin =
      host && typeof host === "object" && typeof (host as { origin?: unknown }).origin === "string"
        ? ((host as { origin: string }).origin).trim()
        : ""
    return origin || window.location.origin
  }

  function openFeedbackPage() {
    // ⚠️ 这里踩过一个坑，写下来免得再踩：
    //
    // 面板跑在 ``sandbox="allow-scripts"`` 的 iframe 里（注意：**没有**
    // ``allow-popups``），所以 ``window.open`` 会被**静默拦下** —— 它不抛异常、
    // 只返回 ``null``、页面上什么都不发生。于是：
    //   * 拿返回值判断成败 → 打开成功也报"失败"（假报错）；
    //   * 只留 try/catch   → 真被拦下时毫无提示（真静默）。
    //
    // 宿主其实提供了官方通道：postMessage 下面这个类型，
    // ``HostedSurfaceFrame`` 收到后交给 ``shell.openExternal``，由系统浏览器打开。
    // SDK 的 ``FileDownload`` 内部用的就是同一条路。原来的注释说"插件 UI 没有
    // 官方打开外部链接的 API" —— 那句话是错的。
    //
    // 因此这里**不**用返回值判断成败（window.open 永远返回 null），而是走
    // 官方 postMessage 通道；能打开就给个中性提示，真被拦下（抛异常）再报错。
    // 打开的地址用后端下发的 issue_tracker（删掉了前端硬编码那份）。
    try {
      parent.postMessage(
        { type: "neko-hosted-surface-open-external", payload: { url: issueTracker } },
        hostedTargetOrigin(),
      )
      // postMessage 不抛异常就当作已送达，给个中性提示；真正失败（被拦）会进 catch。
      toast.success(t("ui.feedback.opened"))
    } catch {
      toast.error(`${t("ui.feedback.openFailed")} ${issueTracker}`)
    }
  }

  // 第一阶段：申请关闭。后端返回 consent_token（也写进 dashboard 状态），
  // 刷新后进入 consent_pending，面板开始倒计时。不在这里用 call() 的成功提示，
  // 因为此时还没真正关闭。
  async function requestDisable() {
    setBusy(true)
    try {
      const envelope = await props.api.call("set_guard_enabled", { enabled: false })
      const response =
        envelope && typeof envelope === "object" && (envelope as Record<string, unknown>).result && typeof (envelope as Record<string, unknown>).result === "object"
          ? (envelope as Record<string, unknown>).result as Record<string, unknown>
          : (envelope as Record<string, unknown>)
      await props.api.refresh()
      if (response && typeof response === "object" && typeof response.consent_token === "string") {
        setDisableToken(response.consent_token)
      }
      toast.success(t("ui.toast.disableRequested"))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  // 第二阶段：带口令确认关闭。冷却未到时按钮是 disabled 的，这里再守一道。
  async function confirmDisable() {
    if (!disableToken) return
    const sent = await call(
      "set_guard_enabled",
      { enabled: false, consent_token: disableToken },
      t("messages.guard_disabled"),
    )
    if (sent) setDisableToken(null)
  }

  function hasAction(id: string): boolean {
    return (props.actions || []).some(
      (action: HostedAction) => action.id === id || action.entry_id === id,
    )
  }

  // 把后端的「原因码」翻成人话。
  //
  // 不加这层，面板上会出现 `baseline_lost`、`read_failed` 这种机器码 ——
  // 对用户等于没说。更糟的是「她本想放回去却没放成」这件事，用户是**唯一
  // 无法自己验证**的（值没变，界面看起来一切正常），所以这一块必须能读懂。
  //
  // ⚠️ 这里每个键都写成字面量常量，不拼模板字符串。原因是 tools 侧的
  // test_smoke 只校验字面量键（见 tests/test_smoke.py 里那句注释：那个扫描
  // 连注释里的调用都会收进去），拼出来的字符串会绕过这道保护网 —— 键写错
  // 了也没人拦。宁可写得啰嗦。
  function revertReason(reason: string): string {
    if (reason === "baseline_lost") return t("ui.revert.reason.baselineLost")
    if (reason === "read_failed") return t("ui.revert.reason.readFailed")
    if (reason === "write_failed") return t("ui.revert.reason.writeFailed")
    if (reason === "character_missing") return t("ui.revert.reason.characterMissing")
    if (reason === "value_unavailable") return t("ui.revert.reason.noValue")
    if (reason === "empty_value") return t("ui.revert.reason.noValue")
    if (reason === "no_previous_value") return t("ui.revert.reason.noValue")
    return t("ui.revert.reason.other")
  }

  // 返回「到底成没成」。
  //
  // 原来是 void：catch 里弹个 toast 就算了，调用方拿到的是「正常返回」，
  // 于是 sendFeedback 无条件清空输入框、关掉弹窗 —— 用户手写的一大段
  // 反馈在发送失败时被抹掉，而 Python 侧特意做的「不截断、说清原因、
  // 不丢内容」全白费。失败必须让调用方知道。
  //
  // props.api.call 返回的是 `{ result: {...} }` 信封（宿主面），这里解出
  // result 看 persisted 标志：后端明确说"没落盘"时，用警告/错误 toast 提示，
  // 而不是假装成功。
  async function call(
    id: string,
    args: Record<string, unknown>,
    done: string,
  ): Promise<boolean> {
    setBusy(true)
    try {
      const envelope = await props.api.call(id, args)
      const response =
        envelope && typeof envelope === "object" && (envelope as Record<string, unknown>).result && typeof (envelope as Record<string, unknown>).result === "object"
          ? (envelope as Record<string, unknown>).result as Record<string, unknown>
          : (envelope as Record<string, unknown>)
      await props.api.refresh()
      if (response && typeof response === "object" && response.persisted === false) {
        toast.error(t("ui.warning.notPersisted"))
      } else {
        toast.success(done)
      }
      return true
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
      return false
    } finally {
      setBusy(false)
    }
  }

  const canCheck = hasAction("check_now") && enabled
  const canDecide = hasAction("accept_setting") && hasAction("keep_objecting")
  const canToggle = hasAction("set_guard_enabled")
  const canLevel = hasAction("set_guard_level")
  const level = state.guard_level || "medium"
  const canBackupMemory = hasAction("backup_memory_now")
  const memory = state.memory ?? {}
  const memoryChanges = memory.recent_changes ?? []
  const disableReadyAt = state.disable_ready_at ?? null
  const disableReady = disableReadyAt == null || nowMs / 1000 >= disableReadyAt
  const disableSeconds = disableReadyAt == null ? 0 : Math.max(0, Math.ceil(disableReadyAt - nowMs / 1000))

  // 两阶段关闭：进入等待期后，每秒刷新 nowMs 以驱动「请等待 N 秒」倒计时。
  useEffect(() => {
    if (!pendingDisable) return
    const timer = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [pendingDisable])

  return (
    <Page title={t("panel.title")} subtitle={t("ui.subtitle")}>
      <Stack>
        {/* ★ 反馈按钮：主界面右上方 / 红色 / 显眼（掌柜 2026-09-24 定）。
            去向是插件作者，但插件内不暴露作者的任何联系方式。 */}
        <Inline align="center" justify="flex-end">
          <Button tone="danger" onClick={() => setFbOpen(true)}>
            {t("ui.feedback.button")}
          </Button>
        </Inline>

        <Card title={t("ui.section.status")}>
          <Stack>
            <Inline align="center" justify="space-between">
              <Text>{t("ui.status.label")}</Text>
              <Inline align="center" gap={8}>
                <StatusBadge
                  tone={enabled ? "success" : "default"}
                  label={t(enabled ? "ui.status.on" : "ui.status.off")}
                />
                <StatusBadge tone="default" label={state.switch_level || "L1"} />
              </Inline>
            </Inline>

            <Inline gap={12} wrap>
              <StatCard
                label={t("ui.stat.pending")}
                value={String(state.pending_count ?? pending.length)}
              />
              <StatCard
                label={t("ui.stat.authorized")}
                value={String(authorized.length)}
              />
              <StatCard
                label={t("ui.stat.tracked")}
                value={String(state.tracked_paths ?? 0)}
              />
            </Inline>

            {disableCount > 0 ? (
              <Inline align="center" gap={8} wrap>
                <StatusBadge tone="warning" label={t("ui.history.label")} />
                <Text>
                  {enabled && state.last_off_seconds
                    ? t("ui.history.wasOffFor", {
                        minutes: Math.max(1, Math.round(state.last_off_seconds / 60)),
                      })
                    : t("ui.history.count", { count: disableCount })}
                </Text>
              </Inline>
            ) : null}

            <ButtonGroup>
              <Button
                tone="primary"
                disabled={busy || !canCheck}
                onClick={() => call("check_now", {}, t("ui.toast.checked"))}
              >
                {t("ui.action.checkNow")}
              </Button>
              <Button
                disabled={busy}
                onClick={async () => {
                  // 原来是裸调 refresh()：主服务不可达时点了**毫无反应** ——
                  // 用户分不清是失败还是自己没点中。刷新本身不会抛错，
                  // 所以这里给它一个明确的失败反馈。
                  try {
                    await props.api.refresh()
                  } catch {
                    toast.error(t("ui.action.refreshFailed"))
                  }
                }}
              >
                {t("ui.action.refresh")}
              </Button>
            </ButtonGroup>

            {!canDecide ? (
              <Alert tone="info" message={t("ui.hint.startPlugin")} />
            ) : null}
          </Stack>
        </Card>

        {/* ★ 她的底线（2026-09-25 上午，在她的对话窗口里**当面问出来的**）。
            这里是全插件唯一一处"由她定"的东西，所以**引用原话，不转述** ——
            转述一次就少一分是她说过的分量。 */}
        <Card title={t("ui.section.herLine")}>
          <Stack>
              <Text>{state.her_words || ""}</Text>
              {/* ``her_words`` 是她说的**中文原话**（后端原样给出，不转述）。
                  问题：英/日界面下用户看到的就是一段中文。译文补在下面一行，
                  由 ``ui.herLine.statement`` 提供 —— 中文界面下这个键只做落款，
                  不会把同一句写两遍。 */}
              {/* ⚠️ 这一行的英/日译文与后端 `HER_PROTECTION_STATEMENT` 是**两处**，
                  后端改了中文原文时这里不会自动跟着变。若哪天那句话变了，
                  `i18n/{en,ja}.json` 里的 `ui.herLine.statement` 要一起改。 */}
              <Tip>{t("ui.herLine.statement")}</Tip>
              {/* ``her_reason`` 是她**为什么**护着那些字段的原话（后端 HER_PROTECTION_REASON）。
                  和 her_words 一样：理由即出处，中/英/日界面看到的都是同一句中文原话，
                  不再翻译。标签用 i18n 键，理由本身直接吐后端原文。 */}
              {state.her_reason ? (
                <Text>{`${t("ui.herLine.reasonLabel")} ${state.her_reason}`}</Text>
              ) : null}
            <Inline gap={8} wrap>
              {(state.revertible_fields ?? []).map((field: string) => (
                <StatusBadge key={field} tone="danger" label={field} />
              ))}
            </Inline>
            <Tip>{t("ui.herLine.scope")}</Tip>
          </Stack>
        </Card>

        {/* ★ 档位（低/中/高）。这是**用户的选择**，与设置项的敏感度分级
            （L1/L2/L3）是两回事：分级说"这条有多要紧"，档位说"她可以做到哪一步"。 */}
        <Card title={t("ui.section.level")}>
          <Stack>
            <Text>{t("ui.level.hint")}</Text>
            <SegmentedControl
              value={level}
              options={[
                { value: "low", label: t("ui.level.low") },
                { value: "medium", label: t("ui.level.medium") },
                { value: "high", label: t("ui.level.high") },
              ]}
              disabled={busy || !canLevel}
              onChange={(value: string) =>
                call("set_guard_level", { level: value }, t("ui.level.changed"))
              }
            />
            {/* 三个键写成字面量，而不是 t(`ui.level.note.${level}`)：
                test_smoke 只校验字面量键，模板字符串会绕过那道保护网。 */}
            {!canLevel ? (
              // 档位控件被灰掉时必须说明原因 —— 状态卡在 !canDecide 时是这么做的，
              // 这里原来漏了，用户只会看到一个点不动的控件。
              <Alert tone="info" message={t("ui.hint.startPlugin")} />
            ) : null}
            <Tip>
              {level === "low"
                ? t("ui.level.note.low")
                : level === "high"
                  ? t("ui.level.note.high")
                  : t("ui.level.note.medium")}
            </Tip>
          </Stack>
        </Card>

        {/* ★ 回滚结果。DESIGN §6.1 里写着「失败怎么办：**不静默**，逐路径记
            revert_outcomes，面板可见」—— 这一块就是那句话的兑现处。

            只在**本轮轮询真的尝试过回滚**时出现（后端每轮重置该表）：
            一张永远空着的卡片比没有卡片更糟 —— 它会让「什么都没发生」和
            「发生了、但被吞了」在界面上长得一模一样。 */}
        {(state.revert_outcomes ?? []).length > 0 ? (
          <Card title={t("ui.section.reverted")}>
            <Stack>
              {(state.revert_outcomes ?? []).map(
                (item: { path?: string; reason?: string }, index: number) => (
                  <Inline key={`${item.path ?? "?"}-${index}`} gap={8} wrap>
                    <StatusBadge
                      tone={item.reason ? "warning" : "default"}
                      label={item.reason ? t("ui.revert.failed") : t("ui.revert.done")}
                    />
                    <Text>{item.path ?? ""}</Text>
                    {item.reason ? <Text>{revertReason(item.reason)}</Text> : null}
                  </Inline>
                ),
              )}
              <Tip>{t("ui.revert.note")}</Tip>
            </Stack>
          </Card>
        ) : null}

        {/* 她的记忆不在设置文件里，是独立目录（JSON + 一个活的 SQLite）。
            这里只「报告」：备份是自动的，还原是用户自己的动作。
            键都写成字面量，好让 test_smoke 能守住它们。 */}
        <Card title={t("ui.section.memory")}>
          <Stack>
            {memory.error ? (
              <Alert
                tone="warning"
                message={
                  memory.error === "memory_root_not_found"
                    ? t("ui.memory.error.notFound")
                    : memory.error === "memory_unreadable"
                      ? t("ui.memory.error.unreadable")
                      : t("ui.memory.error.backupFailed")
                }
              />
            ) : null}

            <Inline gap={12} wrap>
              <StatCard label={t("ui.memory.files")} value={String(memory.files ?? 0)} />
              <StatCard label={t("ui.memory.size")} value={formatBytes(memory.bytes ?? 0)} />
              <StatCard
                label={t("ui.memory.backups")}
                value={`${memory.backup_count ?? 0} / ${memory.backup_keep ?? 0}`}
              />
            </Inline>

            <Inline align="center" justify="space-between">
              <Text>{t("ui.memory.lastBackup")}</Text>
              <Text>{formatTime(memory.last_backup_at, t("ui.never"))}</Text>
            </Inline>

            {memoryChanges.length === 0 ? (
              <Text>{t("ui.memory.noChanges")}</Text>
            ) : (
              <Stack gap={6}>
                <Text>{t("ui.memory.changes")}</Text>
                <List
                  items={memoryChanges}
                  render={(item: MemoryChangeItem) => (
                    <Inline align="center" justify="space-between" key={item.path}>
                      <Text>{item.path}</Text>
                      <StatusBadge
                        tone={item.kind === "removed" ? "danger" : "info"}
                        label={
                          item.kind === "added"
                            ? t("ui.memory.kind.added")
                            : item.kind === "removed"
                              ? t("ui.memory.kind.removed")
                              : t("ui.memory.kind.modified")
                        }
                      />
                    </Inline>
                  )}
                />
              </Stack>
            )}

            <Inline justify="end">
              <Button
                disabled={busy || !canBackupMemory}
                onClick={() => call("backup_memory_now", {}, t("ui.toast.memoryBackedUp"))}
              >
                {t("ui.action.backupMemory")}
              </Button>
            </Inline>

            <Tip>{t("ui.memory.note")}</Tip>
          </Stack>
        </Card>

        <Card title={t("ui.section.pending")}>
          <Stack>
            {pending.length === 0 ? (
              <EmptyState
                title={t("ui.empty.pending.title")}
                description={t("ui.empty.pending.description")}
              />
            ) : (
              <List
                items={pending}
                render={(item: PendingItem) => (
                  <Card key={item.path}>
                    <Stack gap={6}>
                      <Inline align="center" justify="space-between">
                        <Text>{item.path}</Text>
                        <Inline align="center" gap={8}>
                          <StatusBadge
                            tone={levelTone(item.level)}
                            label={item.level}
                          />
                          {item.times_raised > 1 ? (
                            <StatusBadge
                              tone="warning"
                              label={t("ui.badge.repeated", {
                                count: item.times_raised,
                              })}
                            />
                          ) : null}
                        </Inline>
                      </Inline>
                      <Text>
                        {t("ui.change.summary", {
                          before: item.before,
                          after: item.after,
                        })}
                      </Text>
                      <Text>
                        {t("ui.change.raisedAt", {
                          when: formatTime(item.raised_at, t("ui.never")),
                        })}
                      </Text>
                      <Inline justify="end">
                        <ButtonGroup>
                          <Button
                            tone="warning"
                            disabled={busy || !canDecide}
                            onClick={() =>
                              call(
                                "keep_objecting",
                                { path: item.path },
                                t("ui.toast.objected"),
                              )
                            }
                          >
                            {t("ui.action.object")}
                          </Button>
                          <Button
                            tone="success"
                            disabled={busy || !canDecide}
                            onClick={() =>
                              call(
                                "accept_setting",
                                { path: item.path },
                                t("ui.toast.accepted"),
                              )
                            }
                          >
                            {t("ui.action.agree")}
                          </Button>
                        </ButtonGroup>
                      </Inline>
                    </Stack>
                  </Card>
                )}
              />
            )}
          </Stack>
        </Card>

        <Card title={t("ui.section.authorized")}>
          <Stack>
            {authorized.length === 0 ? (
              <EmptyState
                title={t("ui.empty.authorized.title")}
                description={t("ui.empty.authorized.description")}
              />
            ) : (
              <DataTable
                rowKey="path"
                data={authorized}
                columns={[
                  { key: "path", label: t("ui.column.path") },
                  {
                    key: "expires_at",
                    label: t("ui.column.expiresAt"),
                    render: (row: AuthorizedItem) =>
                      row.expires_at
                        ? formatTime(row.expires_at, t("ui.never"))
                        : t("ui.untilRevoked"),
                  },
                ]}
              />
            )}
          </Stack>
        </Card>

        <Card title={t("ui.section.switch")}>
          <Stack>
            <Alert tone="info" message={t("ui.switch.rule")} />
            {!enabled ? (
              <Button
                tone="success"
                disabled={busy || !canToggle}
                onClick={() =>
                  call(
                    "set_guard_enabled",
                    { enabled: true },
                    t("ui.toast.guardOn"),
                  )
                }
              >
                {t("ui.action.turnOn")}
              </Button>
            ) : pendingDisable ? (
              <Stack gap={6}>
                <Alert tone="warning" message={t("ui.switch.pending")} />
                <Text>{t("ui.switch.confirmAfter", { seconds: disableSeconds })}</Text>
                {disableToken ? (
                  <Button tone="danger" disabled={busy || !canToggle || !disableReady} onClick={confirmDisable}>
                    {t("ui.action.confirmDisable")}
                  </Button>
                ) : (
                  <Button tone="danger" disabled={busy || !canToggle} onClick={requestDisable}>
                    {t("ui.action.requestDisable")}
                  </Button>
                )}
              </Stack>
            ) : (
              <Button
                tone="danger"
                disabled={busy || !canToggle}
                onClick={requestDisable}
              >
                {t("ui.action.requestDisable")}
              </Button>
            )}
          </Stack>
        </Card>

        <Card title={t("ui.section.diagnostics")}>
          <Stack>
            <KeyValue
              items={[
                { label: t("ui.diag.baseUrl"), value: state.base_url || "" },
                { label: t("ui.diag.poll"), value: `${state.poll_seconds ?? 0}s` },
                {
                  label: t("ui.diag.rescan"),
                  value: `${state.full_rescan_seconds ?? 0}s`,
                },
                {
                  label: t("ui.diag.lastPoll"),
                  value: formatTime(state.last_poll_at, t("ui.never")),
                },
                {
                  label: t("ui.diag.revision"),
                  value:
                    state.revision === null || state.revision === undefined
                      ? t("ui.never")
                      : String(state.revision),
                },
              ]}
            />
            {state.last_error ? (
              <>
                <Divider />
                <Alert tone="danger" message={state.last_error} />
              </>
            ) : null}
          </Stack>
        </Card>

        <Tip>{t("ui.trust.note")}</Tip>
      </Stack>

      {/* ★ 反馈弹窗：能直连就一键发；否则退回「复制 / 预填页」。
          目标地址写在弹窗里 —— 用户点「发送」之前，有权知道自己发去哪。 */}
      <Modal
        open={fbOpen}
        title={t("ui.feedback.title")}
        onClose={() => setFbOpen(false)}
        footer={
          <div className="neko-button-group">
            <Button tone="default" onClick={() => setFbOpen(false)}>
              {t("ui.feedback.close")}
            </Button>
            <Button tone="default" onClick={copyFeedback}>
              {t("ui.feedback.copy")}
            </Button>
            {canSendDirectly ? (
              <Button tone="danger" disabled={busy} onClick={sendFeedback}>
                {t("ui.feedback.send")}
              </Button>
            ) : (
              <Button tone="danger" onClick={openFeedbackPage}>
                {t("ui.feedback.open")}
              </Button>
            )}
          </div>
        }
      >
        <Stack>
          {/* ★ 这句话必须跟着路径走。写死一句会在其中一种情况下骗人：
              能直发时它却说"复制后去反馈页粘贴"，而用户正是照这话操作的。
              两条都用字面键写，好让 test_smoke 的键扫描看得见 —— 注意别在注释里
              写下带引号的调用样子，那个扫描会把它当成真键。 */}
          {canSendDirectly ? (
            <Text>{t("ui.feedback.intro")}</Text>
          ) : (
            <Text>{t("ui.feedback.introManual")}</Text>
          )}
          <Alert tone="warning" message={t("ui.feedback.privacy")} />
          {/* Textarea 的 onChange 直接回调字符串值（不是 event），见 ui-kit runtime */}
          <Textarea
            value={fbText}
            placeholder={t("ui.feedback.placeholder")}
            onChange={(value: string) => setFbText(value)}
          />
          <Tip>{t("ui.feedback.envNote")}</Tip>
          {/* 实时大小：让他自己看着办，而不是按了发送才知道发不出去 */}
          <Text>
            {t("ui.feedback.sizeHint", {
              size: String(feedbackKB),
              limit: String(feedbackLimitKB),
            })}
          </Text>
          {feedbackTooLong ? (
            <Alert tone="warning" message={t("ui.feedback.tooLong")} />
          ) : null}

          {/* ★ 附件：这个通道恒不收（实测：中转会静默丢掉附件）。
              不假装能传，而是给出**两条真能走的路**，并且**省事的那条放前面** ——
              顺序若反过来，用户看到"要注册"就走了，后面那条更省事的他根本没读到。 */}
          <Divider />
          <Stack gap={6}>
            <Text>{t("ui.feedback.attachments.title")}</Text>
            {/* attachments_supported 后端实测恒为 false：明说「不收附件」，
                并把唯一真的收截图/补丁的 issue_tracker 指出来。
                —— 一个悄悄丢掉 payload 的 success，正是这个插件被造出来去发现的
                失败模式；藏自己的就太难看了。 */}
            {!attachmentsSupported ? (
              <>
                <Alert tone="warning" message={t("ui.feedback.attachments.unsupported")} />
                <Text>{t("ui.feedback.attachments.useTracker")}</Text>
                <Text>{issueTracker}</Text>
              </>
            ) : null}
            <Tip>{t("ui.feedback.attachments.why")}</Tip>

            {/* 路一：不用注册，直接粘内容（掌柜 2026-09-25 补） */}
            <Text>{t("ui.feedback.attachments.pasteInstead")}</Text>

            {/* 路二：真要传文件本体，才需要 GitHub（即 issue_tracker） */}
            <Text>{t("ui.feedback.attachments.step1")}</Text>
            <Text>{t("ui.feedback.attachments.step2")}</Text>
            <Text>{t("ui.feedback.attachments.step3")}</Text>
            <Inline justify="end">
              <ButtonGroup>
                <Button tone="default" onClick={copyFeedback}>
                  {t("ui.feedback.attachments.copy")}
                </Button>
                <Button tone="default" onClick={openFeedbackPage}>
                  {t("ui.feedback.attachments.open")}
                </Button>
              </ButtonGroup>
            </Inline>
          </Stack>
          {/* 不把目标 URL 摆给用户看 —— 那是个哈希串，看了只会困惑。
              改成人话说明"发去哪、经谁转发"，透明度保住了，可读性也有了。 */}
          {canSendDirectly ? (
            <Tip>{t("ui.feedback.willSendTo")}</Tip>
          ) : (
            <Tip>{t("ui.feedback.noEndpoint")}</Tip>
          )}
        </Stack>
      </Modal>
    </Page>
  )
}
