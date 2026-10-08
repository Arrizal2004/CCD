import { useState, useEffect, useCallback } from 'react';
import { fetchProxmoxInstances, createProxmoxInstance, updateProxmoxInstance, deleteProxmoxInstance } from '../api';
import { useT } from '../i18n';

const emptyForm = { label: '', host: '', token_id: '', token_secret: '', verify_ssl: false };

// Panel CRUD Proxmox instance — dipakai langsung sebagai halaman (tab "Instances") maupun
// dibungkus modal (ProxmoxInstancesModal, dipanggil dari tombol "Manage Instances" di Servers).
export default function ProxmoxInstancesPanel({ onChanged }) {
    const t = useT();
    const [instances, setInstances] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [form, setForm] = useState(emptyForm);
    const [editingLabel, setEditingLabel] = useState(null); // null = creating new
    const [saving, setSaving] = useState(false);

    const load = useCallback(async () => {
        try {
            setInstances(await fetchProxmoxInstances());
            setError(null);
        } catch (e) {
            setError(e?.response?.data?.detail || t('inst.loadFailed'));
        } finally {
            setLoading(false);
        }
    }, [t]);

    useEffect(() => { load(); }, [load]);

    const startEdit = (inst) => {
        setEditingLabel(inst.label);
        setForm({ label: inst.label, host: inst.host, token_id: inst.token_id, token_secret: '', verify_ssl: inst.verify_ssl });
    };

    const startNew = () => {
        setEditingLabel('__new__');
        setForm(emptyForm);
    };

    const cancelEdit = () => {
        setEditingLabel(null);
        setForm(emptyForm);
        setError(null);
    };

    const handleSave = async (e) => {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            if (editingLabel === '__new__') {
                if (!form.token_secret) throw { response: { data: { detail: t('inst.secretRequired') } } };
                await createProxmoxInstance(form);
            } else {
                const body = { host: form.host, token_id: form.token_id, verify_ssl: form.verify_ssl };
                if (form.token_secret) body.token_secret = form.token_secret;
                await updateProxmoxInstance(editingLabel, body);
            }
            cancelEdit();
            await load();
            onChanged?.();
        } catch (e) {
            setError(e?.response?.data?.detail || t('inst.saveFailed'));
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async (label) => {
        if (!confirm(t('inst.deleteConfirm', { label }))) return;
        try {
            await deleteProxmoxInstance(label);
            await load();
            onChanged?.();
        } catch (e) {
            setError(e?.response?.data?.detail || t('inst.deleteFailed'));
        }
    };

    const isEditing = editingLabel !== null;

    return (
        <div>
            {error && (
                <div style={{ background: 'var(--red-glow)', border: '1px solid var(--red)', borderRadius: 6, padding: '6px 10px', color: 'var(--red)', fontSize: 11, marginBottom: 12 }}>⚠ {error}</div>
            )}

            {loading && <div style={{ color: 'var(--text3)', fontSize: 12, padding: 10 }}>{t('common.loading')}</div>}

            {!loading && !isEditing && (
                <>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 14 }}>
                        {instances.length === 0 && (
                            <div style={{ fontSize: 12, color: 'var(--text3)', padding: 10 }}>{t('inst.none')}</div>
                        )}
                        {instances.map(inst => (
                            <div key={inst.label} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px' }}>
                                <div>
                                    <div style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'var(--fmono)', fontWeight: 600 }}>{inst.label}</div>
                                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{inst.host} · {inst.token_id} · {inst.verify_ssl ? t('inst.sslOn') : t('inst.sslOff')}</div>
                                </div>
                                <div style={{ display: 'flex', gap: 6 }}>
                                    <button onClick={() => startEdit(inst)}
                                        style={{ padding: '4px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                                        {t('common.edit')}
                                    </button>
                                    <button onClick={() => handleDelete(inst.label)}
                                        style={{ padding: '4px 10px', fontSize: 10, borderRadius: 5, cursor: 'pointer', background: 'transparent', border: '1px solid var(--red)', color: 'var(--red)' }}>
                                        {t('common.delete')}
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                    <button onClick={startNew}
                        style={{ padding: '6px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan-glow)', border: '1px solid var(--cyan)', color: 'var(--cyan)' }}>
                        {t('inst.add')}
                    </button>
                </>
            )}

            {isEditing && (
                <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 420 }}>
                    <div>
                        <div style={{ fontSize: 10, color: 'var(--text3)', marginBottom: 2 }}>{t('inst.label')}</div>
                        <input value={form.label} disabled={editingLabel !== '__new__'}
                            onChange={e => setForm(f => ({ ...f, label: e.target.value }))}
                            placeholder="campus"
                            style={{ width: '100%', boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 }} />
                    </div>
                    <div>
                        <div style={{ fontSize: 10, color: 'var(--text3)', marginBottom: 2 }}>{t('inst.host')}</div>
                        <input value={form.host} onChange={e => setForm(f => ({ ...f, host: e.target.value }))}
                            placeholder="192.168.1.10:8006"
                            style={{ width: '100%', boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 }} />
                    </div>
                    <div>
                        <div style={{ fontSize: 10, color: 'var(--text3)', marginBottom: 2 }}>{t('inst.tokenId')}</div>
                        <input value={form.token_id} onChange={e => setForm(f => ({ ...f, token_id: e.target.value }))}
                            placeholder="root@pam!dashboard"
                            style={{ width: '100%', boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 }} />
                    </div>
                    <div>
                        <div style={{ fontSize: 10, color: 'var(--text3)', marginBottom: 2 }}>
                            {t('inst.tokenSecret')} {editingLabel !== '__new__' && <span style={{ color: 'var(--text3)' }}>{t('inst.keepSecret')}</span>}
                        </div>
                        <input type="password" value={form.token_secret} onChange={e => setForm(f => ({ ...f, token_secret: e.target.value }))}
                            placeholder="••••••••-••••-••••-••••-••••••••••••"
                            style={{ width: '100%', boxSizing: 'border-box', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 12 }} />
                    </div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text2)' }}>
                        <input type="checkbox" checked={form.verify_ssl} onChange={e => setForm(f => ({ ...f, verify_ssl: e.target.checked }))} />
                        {t('inst.verifySsl')}
                    </label>
                    <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                        <button type="submit" disabled={saving}
                            style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--cyan)', color: '#000', border: 'none', fontWeight: 600, opacity: saving ? 0.6 : 1 }}>
                            {saving ? t('common.saving') : t('common.save')}
                        </button>
                        <button type="button" onClick={cancelEdit}
                            style={{ padding: '6px 16px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text3)' }}>
                            {t('common.cancel')}
                        </button>
                    </div>
                </form>
            )}
        </div>
    );
}
