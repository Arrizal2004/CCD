import { useEffect, useRef, useState } from 'react';
import { fetchSystemSettings, saveSystemSettings, uploadSystemLogo, deleteSystemLogo } from '../api';
import { DEFAULT_BRANDING, loadBranding } from '../branding';
import { tNodes, useT } from '../i18n';
import { osIcon } from '../sysconfig';
import BrandLogo from './BrandLogo';
import AnnouncementBanner from './AnnouncementBanner';

// Pengaturan Sistem (superadmin): identitas, tampilan, bahasa, pengumuman, aturan pendaftaran, nilai
// bawaan, kategori tiket, pilihan OS, dan alamat SSH, supaya setiap sekolah atau kampus bisa menyesuaikan
// dashboard ini.
const LIMITS = { name: 60, short_name: 12, institution: 100, tagline: 100 };
const label = { display: 'block', fontSize: 11, color: 'var(--text3)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' };
const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: 'var(--text)', fontSize: 13, outline: 'none' };
const card = { background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, padding: 18, marginBottom: 16 };
const hint = { fontSize: 11, color: 'var(--text3)', marginTop: 4, lineHeight: 1.5 };
const small = { padding: '5px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' };
const title = { fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 14 };

// Warna aksen siap pakai. Semuanya cukup terang supaya teks hitam di tombol tetap terbaca.
const ACCENTS = ['#00e5ff', '#22c55e', '#facc15', '#fb923c', '#f472b6', '#a78bfa', '#60a5fa'];
const LEVELS = [['info', 'sys.levelInfo'], ['warning', 'sys.levelWarning'], ['critical', 'sys.levelCritical']];
const REQUIRED = ['LEASE_EXTENSION', 'OTHERS'];

// ISO UTC <-> nilai <input type="datetime-local"> (waktu lokal browser).
const toLocal = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const fromLocal = (v) => (v ? new Date(v).toISOString() : null);

function Field({ k, name, form, set, placeholder, help }) {
    return (
        <div style={{ marginBottom: 14 }}>
            <label style={label}>{name}</label>
            <input value={form[k]} maxLength={LIMITS[k]} placeholder={placeholder} onChange={e => set(k, e.target.value)} style={input} />
            <div style={hint}>{help}{help ? ' · ' : ''}{form[k].length}/{LIMITS[k]}</div>
        </div>
    );
}

function LogoSection({ form, onChanged, setMsg }) {
    const t = useT();
    const fileRef = useRef(null);
    const [busy, setBusy] = useState(false);
    const upload = async (file) => {
        if (!file) return;
        setBusy(true); setMsg(null);
        try {
            await uploadSystemLogo(file);
            await onChanged();
            setMsg({ ok: true, text: t('sys.logoUpdated') });
        } catch (e) {
            setMsg({ ok: false, text: e?.response?.data?.detail || t('sys.logoFailed') });
        } finally {
            setBusy(false);
            if (fileRef.current) fileRef.current.value = '';
        }
    };
    const remove = async () => {
        if (!confirm(t('sys.logoRemoveConfirm'))) return;
        setBusy(true);
        try { await deleteSystemLogo(); await onChanged(); } finally { setBusy(false); }
    };
    return (
        <div style={{ marginBottom: 14 }}>
            <label style={label}>{t('sys.logo')}</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <BrandLogo size={56} radius={12} branding={form} />
                <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" style={{ display: 'none' }} onChange={e => upload(e.target.files?.[0])} />
                <button disabled={busy} onClick={() => fileRef.current?.click()} style={{ ...small, borderColor: 'var(--cyan)', color: 'var(--cyan)' }}>{busy ? t('sys.uploading') : t('sys.uploadLogo')}</button>
                {form.logo_version && <button disabled={busy} onClick={remove} style={small}>{t('sys.removeLogo')}</button>}
            </div>
            <div style={hint}>{t('sys.logoHint')}</div>
        </div>
    );
}

export default function SystemSettingsPage() {
    const t = useT();
    const [form, setForm] = useState(null);
    const [emails, setEmails] = useState('');
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState(null);   // { ok, text }

    const apply = (s) => {
        setForm(s);
        setEmails((s.allowed_emails || []).join('\n'));
    };

    useEffect(() => {
        let alive = true;
        fetchSystemSettings().then(s => { if (alive) apply(s); })
            .catch(e => { if (alive) setMsg({ ok: false, text: e?.response?.data?.detail || t('sys.loadFailed') }); });
        return () => { alive = false; };
    }, [t]);

    if (!form) return <div style={{ padding: 30, textAlign: 'center', color: msg ? 'var(--red)' : 'var(--text3)' }}>{msg?.text || t('common.loading')}</div>;

    const set = (k, v) => { setForm(f => ({ ...f, [k]: v })); setMsg(null); };
    const setAnn = (k, v) => set('announcement', { ...form.announcement, [k]: v });
    const setCat = (i, v) => set('ticket_categories', form.ticket_categories.map((c, j) => (j === i ? { ...c, label: v } : c)));
    const osList = form.vps_os_options;
    const setOs = (i, v) => set('vps_os_options', osList.map((o, j) => (j === i ? v : o)));
    const moveOsUp = (i) => set('vps_os_options', osList.map((o, j) => (j === i - 1 ? osList[i] : j === i ? osList[i - 1] : o)));
    const rules = emails.split(/[\n,]+/).map(x => x.trim()).filter(Boolean);
    const domains = rules.filter(r => r.startsWith('@') || !r.includes('@'));
    const ann = form.announcement;
    const sshEnv = form.ssh_env || {};
    const sshHost = (form.ssh_public_host || '').trim() || sshEnv.env_host || window.location.hostname;

    const reloadLogo = async () => {
        const s = await fetchSystemSettings();
        setForm(f => ({ ...f, logo_version: s.logo_version }));
        await loadBranding();
    };

    const save = async () => {
        setSaving(true); setMsg(null);
        try {
            // Kategori baru dikirim tanpa kode (dibuatkan server dari namanya); baris baru yang kosong diabaikan.
            const ticket_categories = form.ticket_categories
                .filter(c => c.key || c.label.trim())
                .map(({ key, label }) => ({ key, label }));
            const vps_os_options = form.vps_os_options.map(o => o.trim()).filter(Boolean);
            apply(await saveSystemSettings({ ...form, allowed_emails: rules, ticket_categories, vps_os_options }));
            await loadBranding();   // nama, warna, dan bahasa baru langsung dipakai
            setMsg({ ok: true, text: t('sys.saved') });
        } catch (e) {
            setMsg({ ok: false, text: e?.response?.data?.detail || t('sys.saveFailed') });
        } finally { setSaving(false); }
    };

    const resetIdentity = () => {
        ['name', 'short_name', 'institution', 'tagline'].forEach(k => set(k, DEFAULT_BRANDING[k]));
        set('accent_color', '');
    };

    return (
        <div style={{ padding: '16px 20px', maxWidth: 760, margin: '0 auto' }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 4 }}>{t('sys.heading')}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 16 }}>{t('sys.headingHint')}</div>

            <div style={card}>
                <div style={title}>{t('sys.identity')}</div>
                <Field k="name" name={t('sys.name')} form={form} set={set} placeholder={t('sys.namePh')} help={t('sys.nameHelp')} />
                <Field k="short_name" name={t('sys.shortName')} form={form} set={set} placeholder={t('sys.shortNamePh')} help={t('sys.shortNameHelp')} />
                <Field k="institution" name={t('sys.institution')} form={form} set={set} placeholder={t('sys.institutionPh')} help={t('sys.institutionHelp')} />
                <Field k="tagline" name={t('sys.tagline')} form={form} set={set} placeholder={t('sys.taglinePh')} help={t('sys.taglineHelp')} />
                <LogoSection form={form} onChanged={reloadLogo} setMsg={setMsg} />

                <div style={{ marginBottom: 14 }}>
                    <label style={label}>{t('sys.accent')}</label>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        {ACCENTS.map(c => (
                            <button key={c} onClick={() => set('accent_color', c === ACCENTS[0] ? '' : c)} title={c} aria-label={c}
                                style={{ width: 26, height: 26, borderRadius: '50%', cursor: 'pointer', background: c,
                                    border: (form.accent_color || ACCENTS[0]) === c ? '3px solid var(--text)' : '2px solid var(--border)' }} />
                        ))}
                        <input type="color" value={form.accent_color || ACCENTS[0]} onChange={e => set('accent_color', e.target.value)}
                            style={{ width: 34, height: 28, padding: 0, border: '1px solid var(--border)', borderRadius: 6, background: 'none', cursor: 'pointer' }} title={t('sys.otherColor')} aria-label={t('sys.otherColor')} />
                        <span style={{ fontSize: 12, fontFamily: 'var(--fmono)', color: 'var(--text3)' }}>{form.accent_color || t('sys.defaultColor')}</span>
                    </div>
                    <div style={hint}>{t('sys.accentHint')}</div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14, marginBottom: 14 }}>
                    <div>
                        <label style={label}>{t('sys.language')}</label>
                        <select value={form.default_language} onChange={e => set('default_language', e.target.value)} style={input}>
                            <option value="id">Bahasa Indonesia</option>
                            <option value="en">English</option>
                        </select>
                    </div>
                    <div>
                        <label style={label}>{t('sys.theme')}</label>
                        <select value={form.default_theme} onChange={e => set('default_theme', e.target.value)} style={input}>
                            <option value="dark">{t('sys.themeDark')}</option>
                            <option value="light">{t('sys.themeLight')}</option>
                            <option value="system">{t('sys.themeSystem')}</option>
                        </select>
                    </div>
                </div>
                <div style={{ ...hint, marginTop: -8, marginBottom: 14 }}>{t('sys.langThemeHint')}</div>

                <div style={{ ...label, marginTop: 4 }}>{t('sys.preview')}</div>
                <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: 'var(--bg-card2)', borderBottom: '1px solid var(--border)' }}>
                        <BrandLogo size={26} branding={form} />
                        <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{form.name || '—'}</span>
                        <span style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 'auto', flexShrink: 0 }}>{t('sys.previewPhone', { name: form.short_name || '—' })}</span>
                    </div>
                    <div style={{ padding: '14px', textAlign: 'center' }}>
                        <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>{form.name || '—'}</div>
                        {form.tagline && <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'var(--fmono)', marginTop: 2 }}>{form.tagline}</div>}
                        {form.institution && <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>{form.institution}</div>}
                        <span style={{ display: 'inline-block', marginTop: 10, padding: '6px 16px', borderRadius: 6, fontSize: 12, fontWeight: 700, color: '#000', background: form.accent_color || ACCENTS[0] }}>{t('sys.sampleButton')}</span>
                    </div>
                </div>
                <button onClick={resetIdentity} style={{ ...small, marginTop: 10 }}>{t('sys.resetIdentity')}</button>
            </div>

            <div style={card}>
                <div style={title}>{t('sys.announcement')}</div>
                <label style={label}>{t('sys.annText')}</label>
                <textarea value={ann.text} maxLength={500} rows={3} onChange={e => setAnn('text', e.target.value)}
                    placeholder={t('sys.annPh')} style={{ ...input, resize: 'vertical' }} />
                <div style={hint}>{t('sys.annCount', { n: ann.text.length })}</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginTop: 12 }}>
                    <div>
                        <label style={label}>{t('sys.annLevel')}</label>
                        <select value={ann.level} onChange={e => setAnn('level', e.target.value)} style={input}>
                            {LEVELS.map(([v, l]) => <option key={v} value={v}>{t(l)}</option>)}
                        </select>
                    </div>
                    <div>
                        <label style={label}>{t('sys.annStart')}</label>
                        <input type="datetime-local" value={toLocal(ann.starts_at)} onChange={e => setAnn('starts_at', fromLocal(e.target.value))} style={{ ...input, colorScheme: 'dark' }} />
                    </div>
                    <div>
                        <label style={label}>{t('sys.annEnd')}</label>
                        <input type="datetime-local" value={toLocal(ann.ends_at)} onChange={e => setAnn('ends_at', fromLocal(e.target.value))} style={{ ...input, colorScheme: 'dark' }} />
                    </div>
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)', cursor: 'pointer', marginTop: 12 }}>
                    <input type="checkbox" checked={ann.show_on_login} onChange={e => setAnn('show_on_login', e.target.checked)} />
                    {t('sys.annLogin')}
                </label>
                {ann.text && <AnnouncementBanner announcement={{ ...ann, id: 'preview' }} style={{ marginTop: 12 }} />}
            </div>

            <div style={card}>
                <div style={title}>{t('sys.registration')}</div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)', cursor: 'pointer', marginBottom: 14 }}>
                    <input type="checkbox" checked={form.registration_open} onChange={e => set('registration_open', e.target.checked)} />
                    {t('sys.regOpen')}
                </label>

                <label style={label}>{t('sys.regEmails')}</label>
                <textarea value={emails} onChange={e => { setEmails(e.target.value); setMsg(null); }} rows={5}
                    disabled={!form.registration_open} placeholder={'@student.kampus.ac.id\n@kampus.ac.id\ndosen.tamu@gmail.com'}
                    style={{ ...input, fontFamily: 'var(--fmono)', resize: 'vertical', opacity: form.registration_open ? 1 : 0.5 }} />
                <div style={hint}>
                    {tNodes('sys.regRules', { domain: <b>@domain</b> })}
                    {rules.length > 0 && t('sys.regNow', { n: rules.length })}
                </div>
                <div style={{ ...hint, marginTop: 8 }}>
                    {t('sys.regDomains', { list: domains.length ? ` (${domains.slice(0, 3).join(', ')}${domains.length > 3 ? ', …' : ''})` : '' })}
                </div>
            </div>

            <div style={card}>
                <div style={title}>{t('sys.defaults')}</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 14 }}>
                    <div>
                        <label style={label}>{t('sys.leaseDays')}</label>
                        <input type="number" min="1" max="3650" value={form.default_vm_lease_days ?? ''} placeholder={t('sys.noLimitPh')}
                            onChange={e => set('default_vm_lease_days', e.target.value ? Number(e.target.value) : null)} style={input} />
                        <div style={hint}>{t('sys.leaseHint')}</div>
                    </div>
                    <div>
                        <label style={label}>{t('sys.accountDays')}</label>
                        <input type="number" min="1" max="3650" value={form.default_account_days ?? ''} placeholder={t('sys.noLimitPh')}
                            onChange={e => set('default_account_days', e.target.value ? Number(e.target.value) : null)} style={input} />
                        <div style={hint}>{tNodes('sys.accountHint', { col: <code>expires_at</code> })}</div>
                    </div>
                </div>
            </div>

            <div style={card}>
                <div style={title}>{t('sys.categories')}</div>
                {form.ticket_categories.map((c, i) => (
                    <div key={c.key || c._id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <input value={c.label} maxLength={40} placeholder={t(`cat.${c.key}`) === `cat.${c.key}` ? t('sys.categoryPh') : t(`cat.${c.key}`)}
                            onChange={e => setCat(i, e.target.value)} style={{ ...input, flex: 1 }} />
                        <span style={{ fontSize: 10, fontFamily: 'var(--fmono)', color: 'var(--text3)', width: 130, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.key || t('sys.categoryNew')}</span>
                        <button disabled={REQUIRED.includes(c.key)} onClick={() => set('ticket_categories', form.ticket_categories.filter((_, j) => j !== i))}
                            title={REQUIRED.includes(c.key) ? t('sys.categoryLocked') : t('common.delete')}
                            style={{ ...small, opacity: REQUIRED.includes(c.key) ? 0.35 : 1, cursor: REQUIRED.includes(c.key) ? 'not-allowed' : 'pointer' }}>✕</button>
                    </div>
                ))}
                {form.ticket_categories.length < 20 && (
                    <button onClick={() => set('ticket_categories', [...form.ticket_categories, { key: '', label: '', _id: Math.random().toString(36).slice(2) }])} style={{ ...small, marginTop: 4 }}>{t('sys.addCategory')}</button>
                )}
                <div style={hint}>{t('sys.categoriesHint')}</div>
            </div>

            <div style={card}>
                <div style={title}>{t('sys.osOptions')}</div>
                {osList.map((o, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <span style={{ width: 20, textAlign: 'center', flexShrink: 0 }}>{osIcon(o)}</span>
                        <input value={o} maxLength={40} placeholder={t('sys.osPh')} aria-label={t('sys.osLabel', { n: i + 1 })}
                            onChange={e => setOs(i, e.target.value)} style={{ ...input, flex: 1, minWidth: 0 }} />
                        <button disabled={i === 0} onClick={() => moveOsUp(i)} title={t('sys.moveUp')} aria-label={t('sys.moveUp')}
                            style={{ ...small, opacity: i === 0 ? 0.35 : 1, cursor: i === 0 ? 'default' : 'pointer' }}>↑</button>
                        <button disabled={osList.length === 1} onClick={() => set('vps_os_options', osList.filter((_, j) => j !== i))}
                            title={osList.length === 1 ? t('sys.osMin') : t('common.delete')}
                            style={{ ...small, opacity: osList.length === 1 ? 0.35 : 1, cursor: osList.length === 1 ? 'not-allowed' : 'pointer' }}>✕</button>
                    </div>
                ))}
                {osList.length < 20 && (
                    <button onClick={() => set('vps_os_options', [...osList, ''])} style={{ ...small, marginTop: 4 }}>{t('sys.addOs')}</button>
                )}
                <div style={hint}>{t('sys.osHint')}</div>
            </div>

            <div style={card}>
                <div style={title}>{t('sys.ssh')}</div>
                {!sshEnv.enabled && (
                    <div style={{ ...hint, marginTop: 0, marginBottom: 10, color: 'var(--yellow)' }}>
                        {t('sys.sshDisabled')}
                    </div>
                )}
                <label style={label} htmlFor="ssh-public-host">{t('sys.sshHost')}</label>
                <input id="ssh-public-host" value={form.ssh_public_host || ''} maxLength={253}
                    placeholder={sshEnv.env_host || window.location.hostname}
                    onChange={e => set('ssh_public_host', e.target.value)} style={{ ...input, fontFamily: 'var(--fmono)' }} />
                <div style={hint}>
                    {tNodes('sys.sshHostHint', {
                        http: <code>http://</code>,
                        fallback: sshEnv.env_host
                            ? tNodes('sys.sshFromEnv', { host: <code>{sshEnv.env_host}</code>, var: <code>BASTION_PUBLIC_HOST</code>, file: <code>.env</code> })
                            : t('sys.sshFromBrowser'),
                    })}
                </div>
                <div style={{ ...hint, marginTop: 8 }}>
                    {tNodes('sys.sshCommand', {
                        cmd: <code>ssh -J tunnel@{sshHost}:{sshEnv.port || 2222} &lt;{t('sys.sshCmdAccount')}&gt;@&lt;{t('sys.sshCmdIp')}&gt;</code>,
                        var: <code>BASTION_PUBLIC_PORT</code>, file: <code>.env</code>,
                    })}
                </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <button onClick={save} disabled={saving}
                    style={{ padding: '9px 22px', borderRadius: 8, background: saving ? 'var(--bg-hover)' : 'var(--cyan)', color: saving ? 'var(--text3)' : '#000', fontSize: 13, fontWeight: 700, border: 'none', cursor: saving ? 'wait' : 'pointer' }}>
                    {saving ? t('common.saving') : t('sys.save')}
                </button>
                {msg && <span style={{ fontSize: 12, color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.ok ? '✓' : '⚠'} {msg.text}</span>}
            </div>
        </div>
    );
}
