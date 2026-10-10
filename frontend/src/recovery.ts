/**
 * White-screen recovery.
 *
 * React error boundaries cannot help when the failure happens outside React:
 * a stale Vite hot-reload, a failed chunk import, or a script error before
 * mount all leave an empty #root. This watchdog detects an empty root and
 * injects a styled recovery card directly via the DOM - no React required -
 * so the app can always be recovered with one click.
 */

import { endSession, friendlyError } from "./services/api";

const WATCHDOG_DELAY_MS = 2500;

function rootIsEmpty(): boolean {
  const root = document.getElementById("root");
  return !root || root.children.length === 0;
}

export function renderRecoveryCard(message: string): void {
  const root = document.getElementById("root");
  if (!root || root.querySelector("[data-ss-recovery]")) return;
  root.innerHTML = "";
  const card = document.createElement("div");
  card.setAttribute("data-ss-recovery", "");
  card.style.cssText = [
    "min-height:100vh", "display:flex", "align-items:center", "justify-content:center",
    "background:#06090b", "font-family:'Red Rose',system-ui,sans-serif", "padding:24px",
  ].join(";");
  card.innerHTML = `
    <div style="max-width:460px;width:100%;border-radius:20px;padding:32px;
      background:linear-gradient(135deg,rgba(23,34,38,.7),rgba(13,20,24,.62));
      border:1px solid rgba(255,255,255,.1);box-shadow:0 16px 44px -16px rgba(0,0,0,.55);
      text-align:center;color:#d7e0e2">
      <div style="font-size:28px;font-weight:800;letter-spacing:-0.04em;margin-bottom:6px;
        background:linear-gradient(90deg,#10B981,#06B6D4,#2DD4BF);
        -webkit-background-clip:text;background-clip:text;color:transparent">ScholarScout</div>
      <p style="margin:0 0 4px;font-size:14px;color:#d7e0e2">
        The page hit an unexpected error and could not render.</p>
      <p data-ss-err style="margin:0 0 20px;font-size:12px;color:#8b9ea3;word-break:break-word"></p>
      <button data-ss-reload style="cursor:pointer;border:none;border-radius:999px;
        padding:10px 22px;margin:0 6px 8px;font-size:13px;font-weight:600;color:#04110c;
        background:linear-gradient(90deg,#10B981,#06B6D4)">Reload ScholarScout</button>
      <button data-ss-home style="cursor:pointer;border-radius:999px;padding:10px 22px;margin:0 6px 8px;
        font-size:13px;font-weight:600;color:#d7e0e2;background:rgba(255,255,255,.06);
        border:1px solid rgba(255,255,255,.14)">Go to home</button>
      <p style="margin:12px 0;font-size:12px;color:#8b9ea3">Reloading or leaving requests deletion of this private session.</p>
      <button data-ss-erase style="cursor:pointer;border-radius:999px;padding:10px 22px;font-size:13px;
        color:#fda4af;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14)">End session and erase data</button>
    </div>`;
  const errLine = card.querySelector("[data-ss-err]");
  if (errLine) errLine.textContent = message || "";
  card.querySelector("[data-ss-reload]")?.addEventListener("click", () => {
    window.location.reload();
  });
  card.querySelector("[data-ss-home]")?.addEventListener("click", () => {
    window.location.assign("/");
  });
  const eraseButton = card.querySelector<HTMLButtonElement>("[data-ss-erase]");
  eraseButton?.addEventListener("click", () => {
    if (!eraseButton.dataset.confirm) {
      eraseButton.dataset.confirm = "1";
      eraseButton.textContent = "Yes, erase this session permanently";
      return;
    }
    eraseButton.disabled = true;
    eraseButton.textContent = "Erasing session…";
    void endSession().then(() => window.location.assign("/")).catch((reason) => {
      if (errLine) errLine.textContent = friendlyError(reason);
      eraseButton.disabled = false;
      eraseButton.textContent = "Yes, erase this session permanently";
    });
  });
  root.appendChild(card);
}

export function installAppRecovery(): void {
  // Failed imports reach the error boundaries for retry and erasure. A full
  // automatic reload would end the private session without a user choice.

  const scheduleCheck = (message: string) => {
    window.setTimeout(() => {
      if (rootIsEmpty()) renderRecoveryCard(message);
    }, WATCHDOG_DELAY_MS);
  };

  window.addEventListener("error", (e) => {
    if (rootIsEmpty()) scheduleCheck((e as ErrorEvent)?.message || "");
  });
  window.addEventListener("unhandledrejection", () => {
    if (rootIsEmpty()) scheduleCheck("");
  });
  // initial mount watchdog: covers the "no module ever executed" case
  if (document.readyState === "complete") scheduleCheck("");
  else window.addEventListener("load", () => scheduleCheck(""));
}
