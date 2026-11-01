import { Component, type ErrorInfo, type ReactNode, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // No analytics or trackers: the error stays on the device unless the user copies it.
    console.error("Ghana Farm Intelligence error boundary", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app" style={{ display: "grid", placeItems: "center", padding: 24 }}>
          <div className="callout" style={{ maxWidth: 560 }}>
            <h2>Something went wrong in this view</h2>
            <p className="small muted">
              The map or panel hit an unexpected error. Your saved farms are stored in this browser and are not
              affected. Reload to continue.
            </p>
            <pre className="tiny" style={{ whiteSpace: "pre-wrap", opacity: 0.7 }}>
              {this.state.error.message}
            </pre>
            <div className="button-row">
              <button type="button" className="button button--primary" onClick={() => window.location.reload()}>
                Reload the app
              </button>
              <a className="button button--ghost" href="/api/health" target="_blank" rel="noreferrer">
                Check the backend
              </a>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const container = document.getElementById("root");
if (!container) throw new Error("Root element missing");
createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
