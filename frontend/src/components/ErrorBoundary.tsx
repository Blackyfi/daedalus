import { Component, ErrorInfo, ReactNode } from "react";
import { reportRenderError } from "../diagnostics";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportRenderError(error, {
      componentStack: info.componentStack ?? undefined,
    });
    // Also log to console so it shows in devtools during development.
    // eslint-disable-next-line no-console
    console.error("UI render error:", error, info);
  }

  reset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="m-6 panel border-danger">
        <h2 className="text-sm uppercase tracking-wide text-danger">
          Something broke in the UI
        </h2>
        <p className="mt-2 text-sm text-muted">
          {this.state.error?.message ?? "Unknown error"}
        </p>
        <p className="mt-2 text-xs text-muted">
          The error has been logged to the audit trail (see the Audit page,
          filter by <code>ui.render_error</code>).
        </p>
        <button className="btn mt-3" onClick={this.reset}>
          Try again
        </button>
      </div>
    );
  }
}
