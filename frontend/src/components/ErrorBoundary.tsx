import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Dashboard crashed:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="w-full max-w-md rounded-lg border border-zinc-800 bg-zinc-900 p-6">
          <h1 className="font-semibold">The dashboard hit an error</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Monitoring keeps running on the server. Reload the page to reconnect.
          </p>
          <pre className="mt-4 overflow-auto rounded bg-zinc-950 p-3 text-xs text-red-300">{this.state.error.message}</pre>
          <button
            type="button"
            onClick={() => location.reload()}
            className="mt-4 h-9 rounded-md bg-blue-600 px-3.5 text-sm font-medium text-white hover:bg-blue-500"
          >
            Reload
          </button>
        </div>
      </div>
    )
  }
}
