import { Component } from 'react';

export default class ErrorBoundary extends Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error) {
        return { hasError: true, error };
    }

    componentDidCatch(error, info) {
        console.error('[ErrorBoundary] React crash caught:', error, info);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    justifyContent: 'center', height: '100vh',
                    background: '#0b0f1a', color: '#ff1744', fontFamily: 'monospace', gap: 16
                }}>
                    <div style={{ fontSize: 32 }}>⚠</div>
                    <div style={{ fontSize: 14, color: '#e8f0fe' }}>Terjadi error rendering. Dashboard akan reload...</div>
                    <div style={{ fontSize: 11, color: '#4a6a8a', maxWidth: 500, textAlign: 'center' }}>
                        {this.state.error?.message}
                    </div>
                    <button
                        onClick={() => this.setState({ hasError: false, error: null })}
                        style={{
                            marginTop: 8, padding: '8px 20px', background: '#00e5ff',
                            color: '#000', border: 'none', borderRadius: 6,
                            fontFamily: 'monospace', cursor: 'pointer', fontWeight: 600
                        }}
                    >
                        ↺ Retry
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}
