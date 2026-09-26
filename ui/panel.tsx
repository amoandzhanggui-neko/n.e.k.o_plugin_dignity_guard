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
  //: 记忆备份清单（get_dashboard 现在下发）。每项含名字、创建时间、是否已「保留」。
  //: 后端 set_pinned / list_backups 的 pinned 分支早就在，但此前从没人写它，
  //: 所以「里程碑永久保留」整条承诺不可达；现在 pin_backup entry 才接上。
  backups?: { name: string; created_at: number; pinned: boolean }[]
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
  //: ★ 2026-09-26：她"缺席"了多久（秒），以及缺席期间被改的项数。
  //: 用户能直接把插件禁用 —— 那段时间它真没在跑；但"上次活着是什么时候"记着，
  //: 所以下次启动能如实报出来。见后端 `_note_away_gap`。
  //: null = 正常重启（没缺多久）。
  away_seconds?: number | null
  away_change_count?: number | null
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

/** 把后端的 `L1/L2/L3` 换成普通人看得懂的话。
 *
 * 后端用这三个编号表示「这条设置有多要紧」，那是给代码和流程看的。
 * 面板上直接印 "L1" 等于没说：用户既不知道 L 是什么，也不知道谁比谁大。
 * 所以这里统一翻成「最要紧 / 要紧 / 一般」—— 顺序和一目了然的关系都保住了。
 *
 * ⚠️ 只翻**显示**，不翻数据：发给猫娘的那句话（speech.item 里的 {level}）仍然
 * 由后端拼，改的是用户眼睛看到的那一处。
 */
function levelTag(level: string, t: (key: string) => string): string {
  if (level === "L1") return t("ui.level.tag.high")
  if (level === "L2") return t("ui.level.tag.medium")
  if (level === "L3") return t("ui.level.tag.low")
  return level
}

/** 把设置路径读成人话。
 *
 * 后端给的是 `characters.猫娘.demo.昵称` 这种路径 —— 对写代码的人是精确的，
 * 对普通用户是天书。好消息：**路径里本来就大量是中文**（猫娘 / 昵称 / 厌恶…），
 * 所以只要把分隔点换成箭号、把通配去掉，就已经能读成人话：
 *
 *     characters.猫娘.*.昵称   →   猫娘 › 昵称
 *     conversation.settings.subtitleEnabled   →   conversation › settings › subtitleEnabled
 *
 * ⚠️ 刻意**不翻译英文单词**：路径里的英文段是「标识符」，不是文案 ——
 * 瞎猜一个中文意思（比如把 settings 一律叫「设置」）反而让人对不上号。
 * 翻不动的就原样留着，并在卡片里附一行小字说明这串英文是什么（ui.path.help）。
 */
function friendlyPath(path: string): string {
  return path
    .split(".")
    .map((seg) => seg.trim())
    .filter((seg) => seg !== "*" && seg !== "")
    .join(" › ")
}

/** 备份名（后端给的是 `20260926_104533` 这种时间戳）读成人话。
 *
 * 普通人看到一串数字不知道那是「哪一天的那一份」。能解析成时间就写成
 * 「2026-09-26 10:45」，解析不了就原样返回（宁可难看，也不要编一个时间）。
 */
function friendlyBackupName(name: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})/.exec(name)
  if (!m) return name
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`
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
  // 「她在说什么」这块默认收着：面板一打开就是一堆字，没人读。
  // 先给一句她自己的话，想深究的人再点开。
  const [introOpen, setIntroOpen] = useState(false)
  // ★ 右上角「新手引导」弹窗（2026-09-26 掌柜要求）。
  // 背景：插件的 guide surface 在 `plugin.toml` 里声明了、宿主 core 也认得，
  // 但**宿主详情页没有渲染入口**（前端调 `/plugin/{id}/surfaces`，而后端没实现，
  // 回退路径只把 panel 渲染出来）。与其等上游补路由，不如把引导做进面板自己的右上角 ——
  // panel.tsx 属于插件自己的独立仓库，**不会被上游 `git reset --hard` 冲掉**。
  const [guideOpen, setGuideOpen] = useState(false)
  // 「详细情况」（连接地址 / 多久看一次 / 设置改动过几轮）默认收着。
  // 2026-09-26：这些是排查问题才用得到的东西，普通人一打开面板看到
  // "轮询间隔 20s"只会困惑。收起来，需要的人点一下就能看全。
  const [diagOpen, setDiagOpen] = useState(false)
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

  // 保留 / 取消保留某份记忆备份。call() 内部会 refresh()（重拉 dashboard，
  // pinned 立刻反映），出错时直接把后端 message toast 出来（backup_not_found
  // 的文案里带上了名字，比我们自己另写一句更有用）。成功提示复用已有的
  // messages.backupPinned / messages.backupUnpinned。
  async function pinBackup(name: string, pinned: boolean) {
    await call(
      "pin_backup",
      { backup: name, pinned },
      pinned ? t("messages.backupPinned") : t("messages.backupUnpinned"),
    )
  }

  const canCheck = hasAction("check_now") && enabled
  const canDecide = hasAction("accept_setting") && hasAction("keep_objecting")
  // ★ 「问问她」按钮（2026-09-26 掌柜的设计）。
  // 用户可以关掉"主动搭话"，那时她不会自己开口；而插件又不能替他打开。
  // 所以把"她能不能说"从那个总开关上摘下来 —— **想听就点一下**。
  const canAskHer = hasAction("ask_her")
  const canToggle = hasAction("set_guard_enabled")
  const canLevel = hasAction("set_guard_level")
  const level = state.guard_level || "medium"
  const canBackupMemory = hasAction("backup_memory_now")
  const canPin = hasAction("pin_backup")
  const memory = state.memory ?? {}
  const memoryChanges = memory.recent_changes ?? []
  const backups = memory.backups ?? []
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
        {/* ★ 右上角两个入口：「新手引导」+「反馈」（2026-09-26 掌柜定）。
            为什么并排放在这里：
              · 宿主详情页**没有** guide 的渲染入口（后端 `/surfaces` 未实现，见上方注释），
                所以引导得由面板自己提供 —— 放右上角是最容易被找到的位置；
              · 反馈按钮本来就在这儿，两个入口并列，用户一眼看全"能问什么、能说什么"。
            两处都在 panel.tsx（插件自己的仓库）→ 上游升级不会冲掉。 */}
        <Inline align="center" justify="flex-end" gap={8}>
          <Button tone="default" onClick={() => setGuideOpen(true)}>
            {t("ui.guide.button")}
          </Button>
          <Button tone="default" onClick={() => setFbOpen(true)}>
            {t("ui.feedback.button")}
          </Button>
        </Inline>

        {/* ★ 她的自我介绍 + 展开式说明（掌柜 2026-09-26 定）。
          位置选在状态卡之前、反馈按钮之后 —— 面板最显眼的一段。
          为什么先给一句她的话而不是一整页说明：读字的人少，看她说话的人多。

          ⚠️ 2026-09-26 人话化补：普通用户打开这一页，心里其实只有两个问题 ——
          「这东西管什么？」「要我做什么？」。原来的第一张卡是她的一段独白，
          好看但不回答这两个问题。所以在这张卡的最上面补两行：一句说清它管什么，
          一句说清"你什么都不用做"（后者尤其重要：这一页看起来像待办清单，
          用户会以为欠着什么没处理）。 */}
      <Card title={t("ui.intro.title")}>
        <Stack>
          <Text>{t("ui.whatItDoes")}</Text>
          <Tip>{t("ui.nothingToDo")}</Tip>
          <Divider />
          <Text>{t("ui.intro.her")}</Text>
          {introOpen ? (
            <Stack>
              <Text>{t("onboard.b1")}</Text>
              <Text>{t("onboard.b2")}</Text>
              <Text>{t("onboard.tier.low")}</Text>
              <Text>{t("onboard.tier.medium")}</Text>
              <Text>{t("onboard.tier.high")}</Text>
              <Tip>{t("onboard.next")}</Tip>
            </Stack>
          ) : null}
          <Inline>
            <Button tone="default" onClick={() => setIntroOpen(!introOpen)}>
              {t(introOpen ? "ui.intro.hide" : "ui.intro.more")}
            </Button>
          </Inline>
        </Stack>
      </Card>

      <Card title={t("ui.section.status")}>
          <Stack>
            <Inline align="center" justify="space-between">
              <Text>{t("ui.status.label")}</Text>
              <Inline align="center" gap={8}>
                <StatusBadge
                  tone={enabled ? "success" : "default"}
                  label={t(enabled ? "ui.status.on" : "ui.status.off")}
                />
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

            {/* ★ 2026-09-26：如实报出"我缺席过"。
                用户能把插件直接禁用 —— 那段时间它真没在跑，谁改了什么它看不见。
                但"上次活着是什么时候"是记着的，所以这里能报出缺席时长；
                缺席期间被改的项，恰好就是这次启动后首次轮询发现的那些。
                ⚠️ 这是**如实报告**，不是"防绕过" —— 插件被禁用时它真的无能为力，
                能保证的只有"他做过的事会被看见"。 */}
            {state.away_seconds ? (
              <Alert
                tone="warning"
                message={
                  (state.away_change_count ?? 0) > 0
                    ? t("ui.away.notice", {
                        minutes: String(Math.max(1, Math.round(state.away_seconds / 60))),
                        count: String(state.away_change_count ?? 0),
                      })
                    : t("ui.away.none")
                }
              />
            ) : null}
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
            {/* 两个按钮的区别原来完全靠猜（"立即检查" vs "刷新面板"）。
                对写代码的人是显然的，对普通人是两个近义词。各配一行小字。 */}
            <Stack gap={2}>
              <Tip>{t("ui.action.checkNowHint")}</Tip>
              <Tip>{t("ui.action.refreshHint")}</Tip>
            </Stack>

            {!canDecide ? (
              <Alert tone="info" message={t("ui.hint.startPlugin")} />
            ) : null}
          </Stack>
        </Card>

        <Card title={t("ui.section.pending")}>
          <Stack>
            {/* 这一条说明放在最前面：面板上的「同意 / 反对」按钮很容易被误解成
                「我（用户）同意」，而它表达的其实是**她的态度** —— 用户是在替她表态。
                点错了不是小事（会改掉她的立场），所以必须在按之前就说清楚。 */}
            <Tip>{t("ui.pending.howTo")}</Tip>
            {/* ★「问问她」—— 让用户主动请她开口（2026-09-26）。
                她不会自己说（用户可能关了"主动搭话"），但只要用户点了，她就说。
                这样"她能不能说"就不再依赖那个开关，也不必替用户把它打开。 */}
            {canAskHer ? (
              <Inline justify="start">
                <Button
                  tone="default"
                  disabled={busy}
                  onClick={() => call("ask_her", {}, t("ui.toast.askedHer"))}
                >
                  {t("actions.askHer.label")}
                </Button>
              </Inline>
            ) : null}
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
                        <Text>{friendlyPath(item.path)}</Text>
                        <Inline align="center" gap={8}>
                          {/* 原来这里直接印 item.level（L1/L2/L3）—— 对用户等于没说。
                              换成「最要紧 / 要紧 / 一般」，颜色含义不变。 */}
                          <StatusBadge
                            tone={levelTone(item.level)}
                            label={levelTag(item.level, t)}
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
                      {/* 原始路径用小字附在最下面：界面给人看的是人话版，
                          但用户要跟作者对齐"到底是哪一项"时，需要这串精确名字。
                          上面一行解释那个彩色徽章是什么意思（"最要紧/要紧/一般"）。 */}
                      <Tip>{t("ui.level.tag.help")}</Tip>
                      <Tip>{`${t("ui.path.help")}（${item.path}）`}</Tip>
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
                      {/* 原来直接印文件路径（conversation.json 之类），普通人不知道那是啥。
                          去掉分隔点、保留中文段，至少能读成一串"东西的名字"。 */}
                      <Text>{friendlyPath(item.path)}</Text>
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

            {/* 备份清单：列出每一份记忆备份，给出「保留 / 取消保留」。
                没标记的会按轮换到期被清掉，标记过（pinned）的永久保留。
                后端 set_pinned / list_backups 的 pinned 分支早就在，但此前没人写它，
                所以「里程碑永久保留」整条承诺不可达 —— 现在 pin_backup entry 才接上。

                只显示最近 3 份：普通人不需要翻完 14 份历史，而每份一行 + 一个按钮
                很容易把这张卡撑得很长。要更多就往下看"详细情况"里的完整轮换说明。 */}
            {backups.length > 0 ? (
              <Stack gap={8}>
                <Text>{t("ui.memory.backups.title")}</Text>
                <List
                  items={backups.slice(0, 3)}
                  render={(b: { name: string; created_at: number; pinned: boolean }) => (
                    <Inline align="center" justify="space-between" key={b.name}>
                      <Stack gap={2}>
                        <Inline align="center" gap={6}>
                          {/* 后端给的名字是 20260926_104533 这种时间戳 ——
                              普通人看不懂那是"哪一天的那一份"，读成日期更直接。 */}
                          <Text>{friendlyBackupName(b.name)}</Text>
                          {b.pinned ? (
                            <StatusBadge tone="info" label={t("ui.memory.backups.pinned")} />
                          ) : null}
                        </Inline>
                        <Text>{formatTime(b.created_at, t("ui.never"))}</Text>
                      </Stack>
                      <Button
                        disabled={busy || !canPin}
                        onClick={() => pinBackup(b.name, !b.pinned)}
                      >
                        {b.pinned ? t("ui.memory.backups.release") : t("ui.memory.backups.keep")}
                      </Button>
                    </Inline>
                  )}
                />
                <Tip>{t("ui.memory.backups.hint")}</Tip>
              </Stack>
            ) : null}

            <Inline justify="end">
              <Button
                disabled={busy || !canBackupMemory}
                onClick={() => call("backup_memory_now", {}, t("ui.toast.memoryBackedUp"))}
              >
                {t("ui.action.backupMemory")}
              </Button>
            </Inline>

            <Tip>{t("ui.memory.whereBackup")}</Tip>
            <Tip>{t("ui.memory.note")}</Tip>
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
                  // 列里原来直接显示 preferences.2.xxx 这种路径。改成读得懂的写法，
                  // 精确路径仍在"她有意见的"卡片里作为附注保留。
                  {
                    key: "path",
                    label: t("ui.column.path"),
                    render: (row: AuthorizedItem) => friendlyPath(row.path),
                  },
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
            {/* 补一句"关了会怎样、已经记下的还在不在" —— 这是用户按之前
                唯一真正想知道的事，原来只说了"撤掉保护"，没回答记录的去留。 */}
            <Tip>{t("ui.switch.explain")}</Tip>
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

        {/* 详细情况：默认收起。
            2026-09-26：这些值（连接地址 / 多久看一次 / 设置改动过几轮）是
            给排查问题用的。普通用户看到"轮询间隔 20s"只会困惑"我需要懂这个吗"。
            收起来既保住了透明度（想看点一下就有），又不让第一屏被技术细节占满。 */}
        <Card title={t("ui.section.diagnostics")}>
          <Stack>
            <Tip>{t("ui.diag.hint")}</Tip>
            <Inline>
              <Button tone="default" onClick={() => setDiagOpen(!diagOpen)}>
                {t(diagOpen ? "ui.action.hideDetails" : "ui.action.showDetails")}
              </Button>
            </Inline>
            {diagOpen ? (
              <Stack>
                <KeyValue
                  items={[
                    { label: t("ui.diag.baseUrl"), value: state.base_url || "" },
                    { label: t("ui.diag.poll"), value: `${state.poll_seconds ?? 0} 秒` },
                    {
                      label: t("ui.diag.rescan"),
                      value: `${state.full_rescan_seconds ?? 0} 秒`,
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
                          : `${state.revision} 次`,
                    },
                  ]}
                />
              </Stack>
            ) : null}
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

      {/* ★ 新手引导弹窗（2026-09-26 掌柜要求补上）。
          内容直接复用 onboarding 的键 —— 那批键本来属于独立的 guide surface
          （`ui/onboarding.tsx`），而宿主详情页没有渲染它的入口，
          所以同一套文案在这里再走一遍，保证"引导"确实到得了。
          ⚠️ 这些键都写成了字面量（test_smoke 的扫描只认字面量）。 */}
      <Modal
        open={guideOpen}
        title={t("onboard.title")}
        onClose={() => setGuideOpen(false)}
        footer={
          <div className="neko-button-group">
            <Button tone="default" onClick={() => setGuideOpen(false)}>
              {t("ui.guide.close")}
            </Button>
          </div>
        }
      >
        <Stack>
          <Text>{t("onboard.subtitle")}</Text>
          <Divider />
          <Text>{t("onboard.s1")}</Text>
          <Text>{t("onboard.b1")}</Text>
          <Text>{t("onboard.s2")}</Text>
          <Text>{t("onboard.b2")}</Text>
          <Text>{t("onboard.s3")}</Text>
          <Text>{t("onboard.tier.low")}</Text>
          <Text>{t("onboard.tier.medium")}</Text>
          <Text>{t("onboard.tier.high")}</Text>
          <Tip>{t("onboard.tier.default")}</Tip>
          <Text>{t("onboard.s4")}</Text>
          <Text>{t("onboard.step1.title")}</Text>
          <Text>{t("onboard.step1.body")}</Text>
          <Text>{t("onboard.step2.title")}</Text>
          <Text>{t("onboard.step2.body")}</Text>
          <Text>{t("onboard.step3.title")}</Text>
          <Text>{t("onboard.step3.body")}</Text>
          <Tip>{t("onboard.note")}</Tip>
          <Divider />
          <Text>{t("onboard.quote.title")}</Text>
          <Text>{t("onboard.quote.body")}</Text>
          <Tip>{t("onboard.next")}</Tip>
        </Stack>
      </Modal>

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
