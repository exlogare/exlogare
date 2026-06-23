import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { api } from "../lib/api";
import { toast } from "../lib/toast";

export type ChannelKind = "telegram" | "slack" | "matrix";

interface TelegramInitResponse {
  connection_id: string;
  bot_username: string;
  link_code: string;
  webhook_url: string;
  webhook_registered: boolean;
  instructions: string[];
}

interface Props {
  kind: ChannelKind;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export function AddChannelDialog({ kind, open, onOpenChange, onCreated }: Props) {
  const { t } = useTranslation();

  const [botToken, setBotToken] = useState("");
  const [slackUrl, setSlackUrl] = useState("");
  const [slackChannel, setSlackChannel] = useState("");
  const [matrixHome, setMatrixHome] = useState("");
  const [matrixToken, setMatrixToken] = useState("");
  const [matrixRoom, setMatrixRoom] = useState("");

  const [linkInfo, setLinkInfo] = useState<{
    bot: string;
    code: string;
    webhook_registered: boolean;
  } | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) {
      setBotToken("");
      setSlackUrl("");
      setSlackChannel("");
      setMatrixHome("");
      setMatrixToken("");
      setMatrixRoom("");
      setLinkInfo(null);
      setLoading(false);
    }
  }, [open]);

  async function submitTelegram() {
    setLoading(true);
    try {
      const res = await api<TelegramInitResponse>("/api/integrations/telegram/init", {
        method: "POST",
        body: { bot_token: botToken },
      });
      setLinkInfo({
        bot: res.bot_username,
        code: res.link_code,
        webhook_registered: res.webhook_registered,
      });
      if (!res.webhook_registered) {
        toast.warning(t("integrations.telegram_webhook_failed"));
      } else {
        toast.success(t("onboarding.telegram_connected"));
      }
      onCreated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setLoading(false);
    }
  }

  async function submitSlack() {
    setLoading(true);
    try {
      await api("/api/integrations/slack/webhook-init", {
        method: "POST",
        body: {
          webhook_url: slackUrl,
          channel: slackChannel || undefined,
        },
      });
      toast.success(t("onboarding.slack_connected"));
      onCreated();
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setLoading(false);
    }
  }

  async function submitMatrix() {
    setLoading(true);
    try {
      await api("/api/integrations/matrix/init", {
        method: "POST",
        body: {
          homeserver_url: matrixHome,
          access_token: matrixToken,
          room_id: matrixRoom,
        },
      });
      toast.success(t("onboarding.matrix_connected"));
      onCreated();
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setLoading(false);
    }
  }

  const titleKey =
    kind === "telegram"
      ? "integrations.add_channel_title_telegram"
      : kind === "slack"
        ? "integrations.add_channel_title_slack"
        : "integrations.add_channel_title_matrix";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[90] bg-black/50 backdrop-blur-sm" />
        <Dialog.Content
          className={clsx(
            "fixed left-1/2 top-1/2 z-[95] w-full max-w-md -translate-x-1/2 -translate-y-1/2",
            "rounded-xl border border-slate-200 bg-white p-6 shadow-2xl",
            "dark:border-slate-700 dark:bg-slate-900",
          )}
        >
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-lg font-semibold">{t(titleKey)}</Dialog.Title>
            <Dialog.Close
              className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
              aria-label={t("common.close")}
            >
              <X className="h-5 w-5" />
            </Dialog.Close>
          </div>

          {kind === "telegram" && (
            <div className="space-y-3">
              {linkInfo ? (
                <div className="space-y-2 text-sm">
                  <p className="text-slate-500">
                    {t("integrations.telegram_link_intro", { bot: linkInfo.bot })}
                  </p>
                  <code className="block rounded bg-slate-100 p-2 text-center text-base font-semibold dark:bg-slate-800">
                    /link {linkInfo.code}
                  </code>
                  <p className="text-xs text-slate-500">
                    {t("integrations.telegram_link_hint")}
                  </p>
                  <div className="flex items-center justify-end pt-2">
                    <button
                      className="btn-primary"
                      onClick={() => onOpenChange(false)}
                    >
                      {t("integrations.done")}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <label className="label">{t("integrations.telegram_token_label")}</label>
                  <input
                    className="input"
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder="123456:ABC-DEF..."
                  />
                  <div className="flex items-center justify-end gap-2 pt-2">
                    <button className="btn-secondary" onClick={() => onOpenChange(false)}>
                      {t("common.cancel")}
                    </button>
                    <button
                      className="btn-primary inline-flex items-center gap-2"
                      disabled={!botToken || loading}
                      onClick={submitTelegram}
                    >
                      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                      {t("common.connect")}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {kind === "slack" && (
            <div className="space-y-3">
              <div>
                <label className="label">{t("integrations.slack_url_label")}</label>
                <input
                  className="input"
                  value={slackUrl}
                  onChange={(e) => setSlackUrl(e.target.value)}
                  placeholder="https://hooks.slack.com/services/..."
                />
              </div>
              <div>
                <label className="label">{t("integrations.slack_channel_label")}</label>
                <input
                  className="input"
                  value={slackChannel}
                  onChange={(e) => setSlackChannel(e.target.value)}
                  placeholder="#alerts"
                />
              </div>
              <div className="flex items-center justify-end gap-2 pt-2">
                <button className="btn-secondary" onClick={() => onOpenChange(false)}>
                  {t("common.cancel")}
                </button>
                <button
                  className="btn-primary inline-flex items-center gap-2"
                  disabled={!slackUrl || loading}
                  onClick={submitSlack}
                >
                  {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                  {t("common.connect")}
                </button>
              </div>
            </div>
          )}

          {kind === "matrix" && (
            <div className="space-y-3">
              <div>
                <label className="label">{t("integrations.matrix_homeserver_label")}</label>
                <input
                  className="input"
                  value={matrixHome}
                  onChange={(e) => setMatrixHome(e.target.value)}
                  placeholder="https://matrix.example.org"
                />
              </div>
              <div>
                <label className="label">{t("integrations.matrix_token_label")}</label>
                <input
                  type="password"
                  className="input"
                  value={matrixToken}
                  onChange={(e) => setMatrixToken(e.target.value)}
                  placeholder="syt_..."
                />
              </div>
              <div>
                <label className="label">{t("integrations.matrix_room_label")}</label>
                <input
                  className="input"
                  value={matrixRoom}
                  onChange={(e) => setMatrixRoom(e.target.value)}
                  placeholder="!roomid:example.org"
                />
              </div>
              <div className="flex items-center justify-end gap-2 pt-2">
                <button className="btn-secondary" onClick={() => onOpenChange(false)}>
                  {t("common.cancel")}
                </button>
                <button
                  className="btn-primary inline-flex items-center gap-2"
                  disabled={!matrixHome || !matrixToken || !matrixRoom || loading}
                  onClick={submitMatrix}
                >
                  {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                  {t("common.connect")}
                </button>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export default AddChannelDialog;
