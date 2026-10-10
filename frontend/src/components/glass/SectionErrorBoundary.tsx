import { Component, type ReactNode } from "react";
import { FlaskConical } from "lucide-react";

interface State {
  error: Error | null;
}

/** Catches render crashes inside a dashboard section and offers recovery
 * instead of a blank panel. */
export class SectionErrorBoundary extends Component<{ children: ReactNode; name: string; onRetry?: () => void }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="glass p-6">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-rose-300">
            <FlaskConical size={15} /> The {this.props.name} section hit an error
          </div>
          <p className="text-xs text-slate-400">{this.state.error.message}</p>
          <button
            onClick={() => { this.props.onRetry?.(); this.setState({ error: null }); }}
            className="btn-ghost mt-3 px-4 py-1.5 text-xs"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
