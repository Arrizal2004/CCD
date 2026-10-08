import { useT } from '../i18n';

// Tab "Detail | Chat" untuk modal dua panel (Helpdesk, Request) di layar HP. Di desktop kedua panel
// tampil berdampingan; di HP lebarnya tidak cukup, jadi hanya satu panel yang tampil.
export default function PaneTabs({ value, onChange, chatCount = 0 }) {
    const t = useT();
    const tabs = [['detail', t('pane.detail')], ['chat', chatCount ? t('pane.chatCount', { n: chatCount }) : t('pane.chat')]];
    return (
        <div role="tablist" style={{ display: 'flex', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
            {tabs.map(([id, label]) => (
                <button key={id} role="tab" aria-selected={value === id} onClick={() => onChange(id)}
                    style={{
                        flex: 1, padding: '10px 0', fontSize: 13, cursor: 'pointer', background: 'transparent', border: 'none',
                        borderBottom: `2px solid ${value === id ? 'var(--cyan)' : 'transparent'}`,
                        color: value === id ? 'var(--cyan)' : 'var(--text3)', fontWeight: value === id ? 600 : 400,
                    }}>
                    {label}
                </button>
            ))}
        </div>
    );
}
