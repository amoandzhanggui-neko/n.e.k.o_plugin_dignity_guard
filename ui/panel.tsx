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
  // 与 Python 侧同一口径：UTF-8 编码后的字节数（中文一个字 = 3 字节）。
  // 摆在这儿是为了让用户**边写边看见**，而不是写完按了发送才发现发不出去。
  // 与服务端同一个口径：上限属于**整个 HTTP body**（64 KB）。扣掉诊断报告
  // （实测 522 字节）和 JSON 包装后，正文仍有 63 KB 可用。
  // 标"能写满的最大值"而不是保守值 —— 标低了不会报错，只会让人少写，
  // 而那是一种看不见的损失。
  const FEEDBACK_LIMIT_KB = 63
  const feedbackBytes =
    typeof TextEncoder !== "undefined" ? new TextEncoder().encode(fbText).length : fbText.length
  const feedbackKB = Math.round((feedbackBytes / 1024) * 10) / 10
  const feedbackTooLong = feedbackBytes > FEEDBACK_LIMIT_KB * 1024
  // 没有接收地址时的退路：插件仓库的 Issues 预填页。
  // ⚠️ 仓库尚未创建，建好后把这里换成真实链接。
  const FALLBACK_URL =
    "https://github.com/amoandzhanggui-neko/n.e.k.o_plugin_dignity_guard/issues/new"

  /** 组装要提交的正文：用户写的内容 + 自动附带的环境信息 */
  function buildFeedbackBody(): string {
    const env = [
      "--- 以下为自动附带的环境信息（便于定位问题）---",
      "插件: dignity_guard (尊严守卫) v0.1.0",
      `守卫状态: ${enabled ? "已开启" : "已关闭"}`,
      `档位: ${state.guard_level || "medium"}`,
      `正在盯住的设置项: ${state.tracked_paths ?? 0}`,
      `她不认可的项: ${state.pending_count ?? pending.length}`,
      state.last_error ? `最近错误: ${state.last_error}` : null,
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
    const ok = await clipboard.write(buildFeedbackBody())
    if (ok) toast.success(t("ui.feedback.copied"))
  }

  async function sendFeedback() {
    if (!fbText.trim()) {
      toast.error(t("ui.feedback.needText"))
      return
    }
    // 真正发出去的那一步在 Python 侧（action: submit_feedback）。
    // 面板只把用户写的内容递过去，报告由插件自己拼，用户不用管。
    await call("submit_feedback", { message: fbText.trim() }, t("ui.feedback.sent"))
    setFbText("")
    setFbOpen(false)
  }

  function openFeedbackPage() {
    // 没有接收地址时的退路。插件 UI 没有官方的「打开外部链接」API，
    // 先试 window.open；被沙箱拦下时不静默失败 —— 把地址显示出来。
    try {
      const opened = window.open(FALLBACK_URL, "_blank", "noopener,noreferrer")
      if (!opened) toast.error(`${t("ui.feedback.openFailed")} ${FALLBACK_URL}`)
    } catch {
      toast.error(`${t("ui.feedback.openFailed")} ${FALLBACK_URL}`)
    }
  }

  function hasAction(id: string): boolean {
    return (props.actions || []).some(
      (action: HostedAction) => action.id === id || action.entry_id === id,
    )
  }

  async function call(id: string, args: Record<string, unknown>, done: string) {
    setBusy(true)
    try {
      await props.api.call(id, args)
      await props.api.refresh()
      toast.success(done)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
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
                    ? t("ui.history.wasOffFor").replace(
                        "{minutes}",
                        String(Math.max(1, Math.round(state.last_off_seconds / 60))),
                      )
                    : t("ui.history.count").replace(
                        "{count}",
                        String(disableCount),
                      )}
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
              <Button disabled={busy} onClick={() => props.api.refresh()}>
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
            <Tip>{t("ui.herLine.reason")}</Tip>
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
            <Tip>
              {level === "low"
                ? t("ui.level.note.low")
                : level === "high"
                  ? t("ui.level.note.high")
                  : t("ui.level.note.medium")}
            </Tip>
          </Stack>
        </Card>

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
              <Alert tone="warning" message={t("ui.switch.pending")} />
            ) : (
              <Button
                tone="danger"
                disabled={busy || !canToggle}
                onClick={() =>
                  call(
                    "set_guard_enabled",
                    { enabled: false },
                    t("ui.toast.disableRequested"),
                  )
                }
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
              limit: String(FEEDBACK_LIMIT_KB),
            })}
          </Text>
          {feedbackTooLong ? (
            <Alert tone="warning" message={t("ui.feedback.tooLong")} />
          ) : null}

          {/* ★ 附件：这条通道不收（实测：中转会静默丢掉附件）。
              不假装能传，而是给出**两条真能走的路**，并且**省事的那条放前面** ——
              顺序若反过来，用户看到"要注册"就走了，后面那条更省事的他根本没读到。 */}
          <Divider />
          <Stack gap={6}>
            <Text>{t("ui.feedback.attachments.title")}</Text>
            <Tip>{t("ui.feedback.attachments.why")}</Tip>

            {/* 路一：不用注册，直接粘内容（掌柜 2026-09-25 补） */}
            <Text>{t("ui.feedback.attachments.pasteInstead")}</Text>

            {/* 路二：真要传文件本体，才需要 GitHub */}
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
