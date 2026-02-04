import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface Props {
    children: ReactNode;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

class ErrorBoundary extends Component<Props, State> {
    public state: State = {
        hasError: false,
        error: null
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('Uncaught error:', error, errorInfo);
    }

    public render() {
        if (this.state.hasError) {
            return (
                <div className="min-h-screen bg-[#050510] flex items-center justify-center p-6 font-orbitron">
                    <div className="max-w-md w-full glass-panel p-8 border border-red-500/50 shadow-[0_0_50px_rgba(255,0,0,0.2)]">
                        <h1 className="text-red-500 text-2xl font-bold mb-4 tracking-tighter uppercase">System Malfunction</h1>
                        <p className="text-gray-400 font-mono text-sm mb-6">
                            A CRITICAL EXCEPTION HAS OCCURRED IN THE NEURAL INTERFACE.
                            REBOOTING COMPONENT CORE...
                        </p>
                        <div className="bg-black/50 p-4 rounded border border-red-500/20 mb-6 overflow-hidden">
                            <code className="text-xs text-red-400 break-words">
                                {this.state.error?.message}
                            </code>
                        </div>
                        <button
                            onClick={() => window.location.reload()}
                            className="w-full py-3 bg-red-500/10 border border-red-500 text-red-500 font-bold hover:bg-red-500 hover:text-white transition-all duration-300"
                        >
                            INITIALIZE RECOVERY
                        </button>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
