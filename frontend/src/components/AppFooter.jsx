// Global muted footer — academic copyright / attribution line.
// Kept subtle (small, low-emphasis) so it never competes with the metrics charts.

export default function AppFooter() {
    return (
        <footer style={{
            borderTop: '1px solid var(--border)',
            background: 'var(--bg-panel)',
            padding: '10px 20px',
            textAlign: 'center',
            opacity: 0.7
        }}>
            <span style={{
                fontSize: 11,
                color: 'var(--text3)',
                fontFamily: 'var(--fmono)',
                letterSpacing: '0.01em'
            }}>
                © 2026 Campus Cloud Dashboard · Open source under MIT License
            </span>
        </footer>
    );
}
