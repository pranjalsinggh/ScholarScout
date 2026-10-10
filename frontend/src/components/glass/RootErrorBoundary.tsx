import { Component, type ReactNode } from "react";
import { endSession, friendlyError } from "../../services/api";

interface State {
  error: Error | null;
  confirmErase: boolean;
  erasing: boolean;
  eraseError: string;
}

/** Last-resort boundary around the whole app. Without it, any uncaught
 * render crash unmounts React and leaves a permanent blank page. */
export class RootErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, confirmErase: false, erasing: false, eraseError: "" };

  static getDerivedStateFromError(error: Error): State {
    return { error, confirmErase: false, erasing: false, eraseError: "" };
  }

  private eraseSession = async () => {
    if (this.state.erasing) return;
    if (!this.state.confirmErase) { this.setState({ confirmErase: true }); return; }
    this.setState({ erasing: true, eraseError: "" });
    try { await endSession(); window.location.assign("/"); }
    catch (reason) { this.setState({ erasing: false, eraseError: friendlyError(reason) }); }
  };

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    // visible in devtools; keeps the bug diagnosable after the user recovers
    console.error("ScholarScout crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="grid min-h-screen place-items-center bg-[#06090b] p-6">
          <div className="glass w-full max-w-md p-8 text-center">
            <div className="gradient-text mb-2 text-3xl font-extrabold tracking-tight">
              ScholarScout
            </div>
            <p className="text-sm text-slate-200">
              Something crashed while rendering the page.
            </p>
            <p className="mt-2 break-words text-xs text-slate-500">
              {this.state.error.message || "Unknown error"}
            </p>
            <div className="mt-6 flex items-center justify-center gap-3">
              <button
                onClick={() => window.location.reload()}
                className="btn-primary px-5 py-2 text-sm"
              >
                Reload ScholarScout
              </button>
              <button
                onClick={() => {
                  window.location.assign("/");
                }}
                className="btn-ghost px-5 py-2 text-sm"
              >
                Go to home
              </button>
            </div>
            <p className="mt-3 text-xs text-slate-400">Reloading or leaving requests deletion of this private session. You can also erase it directly below.</p>
            <button type="button" onClick={() => void this.eraseSession()} disabled={this.state.erasing}
              className="btn-ghost mt-4 px-5 py-2 text-sm disabled:opacity-50">
              {this.state.erasing ? "Erasing session…" : this.state.confirmErase ? "Yes, erase this session permanently" : "End session and erase data"}
            </button>
            {this.state.confirmErase && !this.state.erasing && <button type="button" className="btn-ghost ml-2 mt-4 px-4 py-2 text-sm" onClick={() => this.setState({ confirmErase: false })}>Cancel</button>}
            {this.state.eraseError && <p role="alert" className="mt-3 text-xs text-rose-300">{this.state.eraseError}</p>}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
