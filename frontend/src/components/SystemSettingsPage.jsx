import { useEffect, useRef, useState } from 'react';
import { fetchSystemSettings, saveSystemSettings, fetchAuditStats, uploadSystemLogo, deleteSystemLogo } from '../api';
import { DEFAULT_BRANDING, loadBranding } from '../branding';
import { locale, tNodes, useT } from '../i18n';
import { appTimeZone, loadSysConfig } from '../sysconfig';
import Clock from './Clock';
import { formatBytes } from '../format';
import BrandLogo from './BrandLogo';
import AnnouncementBanner from './AnnouncementBanner';

// Pengaturan Sistem (superadmin): identitas, tampilan, bahasa, zona waktu, pengumuman, aturan pendaftaran,
// nilai bawaan, lama penyimpanan log audit, dan alamat SSH, supaya setiap sekolah atau kampus bisa
// menyesuaikan dashboard ini. Kategori tiket diatur dari halaman Helpdesk dan pilihan OS dari Infra Requests.
const LIMITS = { name: 60, short_name: 12, institution: 100, tagline: 100 };
const label = { display: 'block', fontSize: 11, color: 'var(--text3)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' };
const input = { width: '100%', boxSizing: 'border-box', background: 'var(--bg-card2)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: 'var(--text)', fontSize: 13, outline: 'none' };
const card = { background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, padding: 18, marginBottom: 16 };
const hint = { fontSize: 11, color: 'var(--text3)', marginTop: 4, lineHeight: 1.5 };
const small = { padding: '5px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text2)' };
const title = { fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 14 };

// Zona waktu Indonesia di urutan pertama; sisanya dari daftar zona yang dikenal browser.
const INDONESIA_ZONES = [['Asia/Jakarta', 'WIB'], ['Asia/Makassar', 'WITA'], ['Asia/Jayapura', 'WIT']];
const allZones = (typeof Intl !== 'undefined' && Intl.supportedValuesOf) ? Intl.supportedValuesOf('timeZone') : [];

// Warna aksen siap pakai. Semuanya cukup terang supaya teks hitam di tombol tetap terbaca.
const ACCENTS = ['#00e5ff', '#22c55e', '#facc15', '#fb923c', '#f472b6', '#a78bfa', '#60a5fa'];
const LEVELS = [['info', 'sys.levelInfo'], ['warning', 'sys.levelWarning'], ['critical', 'sys.levelCritical']];

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
    const [auditStats, setAuditStats] = useState(null);

    const apply = (s) => {
        setForm(s);
        setEmails((s.allowed_emails || []).join('\n'));
    };

    useEffect(() => {
        let alive = true;
        fetchSystemSettings().then(s => { if (alive) apply(s); })
            .catch(e => { if (alive) setMsg({ ok: false, text: e?.response?.data?.detail || t('sys.loadFailed') }); });
        fetchAuditStats().then(s => { if (alive) setAuditStats(s); }).catch(() => {});
        return () => { alive = false; };
    }, [t]);

    if (!form) return <div style={{ padding: 30, textAlign: 'center', color: msg ? 'var(--red)' : 'var(--text3)' }}>{msg?.text || t('common.loading')}</div>;

    const set = (k, v) => { setForm(f => ({ ...f, [k]: v })); setMsg(null); };
    const setAnn = (k, v) => set('announcement', { ...form.announcement, [k]: v });
    const rules = emails.split(/[\n,]+/).map(x => x.trim()).filter(Boolean);
    const domains = rules.filter(r => r.startsWith('@') || !r.includes('@'));
    const ann = form.announcement;
    const sshEnv = form.ssh_env || {};
    // Alamat dari .env dan domain dashboard sengaja tidak ditampilkan di sini; kosong memakai contoh umum.
    const sshHost = (form.ssh_public_host || '').trim() || `<${t('sys.sshCmdHost')}>`;

    const reloadLogo = async () => {
        const s = await fetchSystemSettings();
        setForm(f => ({ ...f, logo_version: s.logo_version }));
        await loadBranding();
    };

    const save = async () => {
        setSaving(true); setMsg(null);
        try {
            apply(await saveSystemSettings({ ...form, allowed_emails: rules }));
            await loadSysConfig();  // zona waktu baru langsung dipakai jam dan semua waktu yang tampil
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
                <div style={title}>{t('sys.audit')}</div>
                <label style={label} htmlFor="audit-retention">{t('sys.auditDays')}</label>
                <input id="audit-retention" type="number" min="7" max="3650" value={form.audit_retention_days ?? ''}
                    placeholder={auditStats ? t('sys.auditPh', { n: auditStats.env_default }) : ''}
                    onChange={e => set('audit_retention_days', e.target.value ? Number(e.target.value) : null)}
                    style={{ ...input, maxWidth: 240 }} />
                <div style={hint}>{t('sys.auditHint')}</div>
                {auditStats && (
                    <div style={{ ...hint, marginTop: 8 }}>
                        {auditStats.rows > 0
                            ? t('sys.auditNow', {
                                n: auditStats.rows.toLocaleString(locale()),
                                date: new Date(auditStats.oldest).toLocaleDateString(locale(), { timeZone: appTimeZone(), day: 'numeric', month: 'short', year: 'numeric' }),
                                size: formatBytes(auditStats.bytes),
                            })
                            : t('sys.auditEmpty')}
                    </div>
                )}
            </div>

            <div style={card}>
                <div style={title}>{t('sys.timezone')}</div>
                <label style={label} htmlFor="sys-timezone">{t('sys.timezoneLabel')}</label>
                <select id="sys-timezone" value={form.timezone || 'Asia/Jakarta'} onChange={e => set('timezone', e.target.value)} style={input}>
                    <optgroup label={t('sys.tzIndonesia')}>
                        {INDONESIA_ZONES.map(([id, name]) => <option key={id} value={id}>{name} ({id})</option>)}
                    </optgroup>
                    <optgroup label={t('sys.tzOthers')}>
                        {allZones.filter(z => !INDONESIA_ZONES.some(([id]) => id === z)).map(z => <option key={z} value={z}>{z}</option>)}
                    </optgroup>
                </select>
                <div style={{ marginTop: 10, fontSize: 13 }}>
                    <span style={{ color: 'var(--text3)', fontSize: 11, marginRight: 8 }}>{t('sys.tzNow')}</span>
                    <Clock timeZone={form.timezone || 'Asia/Jakarta'} style={{ fontSize: 13 }} />
                </div>
                <div style={hint}>{t('sys.timezoneHint')}</div>
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
                    placeholder={t('sys.sshHostPh')}
                    onChange={e => set('ssh_public_host', e.target.value)} style={{ ...input, fontFamily: 'var(--fmono)' }} />
                <div style={hint}>
                    {tNodes('sys.sshHostHint', {
                        http: <code>http://</code>,
                        fallback: sshEnv.env_host_set
                            ? tNodes('sys.sshFromEnv', { var: <code>BASTION_PUBLIC_HOST</code>, file: <code>.env</code> })
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
