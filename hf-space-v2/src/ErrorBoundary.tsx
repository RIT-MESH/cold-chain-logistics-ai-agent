import { Component, ReactNode } from "react";

// Error boundary: any render failure degrades to a visible error card
// with a retry, never a white screen (P2: error states).
export class ErrorBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) { return { err }; }
  render() {
    if (this.state.err)
      return <div className="card error" role="alert">
        <h3>Something went wrong rendering this view</h3>
        <p>{this.state.err.message}</p>
        <button onClick={() => this.setState({ err: null })}>Retry</button>
      </div>;
    return this.props.children;
  }
}
