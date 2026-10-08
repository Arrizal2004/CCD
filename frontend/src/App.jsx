import { useState, useEffect } from 'react';
import LoginPage, { ForcePasswordChange } from './pages/LoginPage';
import UsersPage from './pages/UserPage';
import { useNavigate, useLocation } from 'react-router-dom';
import { resetSessionExpired, logoutApi, fetchPasswordHelp } from './api';
import axios from 'axios';
import ProxmoxTopology from './components/ProxmoxTopology';
import AdminPanel from './components/AdminPanel';
import TicketsPage from './components/TicketsPage';
import InfraRequestsPage from './components/InfraRequestsPage';
import AboutModal from './components/AboutModal';
import AppFooter from './components/AppFooter';
import ProfileModal from './components/ProfileModal';
import StatusPage from './components/StatusPage';
import GroupsPage from './components/GroupsPage';
import ProxmoxPage from './pages/ProxmoxPage';
import OpenWebPage from './pages/OpenWebPage';
import ProxmoxInstancesPanel from './components/ProxmoxInstancesPanel';
import SystemSettingsPage from './components/SystemSettingsPage';
import { useBranding } from './branding';
import { useT } from './i18n';
import { loadSysConfig, useSysConfig } from './sysconfig';
import BrandLogo from './components/BrandLogo';
import LanguageToggle from './components/LanguageToggle';
import ThemeToggle from './components/ThemeToggle';
import AnnouncementBanner from './components/AnnouncementBanner';
import ForbiddenPage from './components/ForbiddenPage';
import Clock from './components/Clock';

// Role badge colors
const ROLE_COLOR = {
    superadmin: '#ff6b35',
    admin: 'var(--cyan)',
    sysadmin: 'var(--green)',
    student: 'var(--purple)',
};

export default function App() {
    const [authUser, setAuthUser] = useState(() => {
        try { return JSON.parse(localStorage.getItem('hv_user')); } catch { return null; }
    });
    const [loginNotice, setLoginNotice] = useState('');
    // Password sementara dari admin, hanya di memori, supaya tidak perlu diketik ulang di layar ganti password.
    const [tempPassword, setTempPassword] = useState('');
    const [pwHelpCount, setPwHelpCount] = useState(0);
    const [showAbout, setShowAbout] = useState(false);
    const [showProfile, setShowProfile] = useState(false);
    const branding = useBranding();
    const t = useT();
    const sysConfig = useSysConfig();

    const navigate = useNavigate();
    const location = useLocation();
    const activeTab = location.pathname === '/topology' ? 'topology' :
        location.pathname === '/users'    ? 'users'    :
        location.pathname === '/groups'   ? 'groups'   :
        location.pathname === '/admin'    ? 'admin'    :
        location.pathname === '/tickets'  ? 'tickets'  :
        location.pathname === '/requests' ? 'requests' :
        location.pathname === '/status'   ? 'status'   :
        location.pathname === '/openweb'  ? 'openweb'  :
        location.pathname === '/system'   ? 'system'   :
        location.pathname === '/instances' ? 'instances' : 'servers';
    const setTab = (tab) => navigate(`/${tab}`);

    useEffect(() => {
        document.querySelector('.ccd-tab[data-active="true"]')?.scrollIntoView({ inline: 'center', block: 'nearest' });
    }, [activeTab]);

    // Student yang belum verified hanya boleh akses /requests
    const isRestrictedStudent = authUser?.role === 'student' && authUser?.is_verified === false;
    useEffect(() => {
        if (isRestrictedStudent && activeTab !== 'requests') navigate('/requests', { replace: true });
    }, [isRestrictedStudent, activeTab, navigate]);


    const handleLogin = (user, token, knownPassword) => {
        axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
        resetSessionExpired();   // izinkan deteksi sesi-habis berikutnya
        setLoginNotice('');
        setTempPassword(knownPassword || '');
        setAuthUser(user);
    };

    const handleLogout = async () => {
        const guacToken = localStorage.getItem('hv_guac_token');
        await logoutApi();
        if (guacToken) {
            fetch(`/guacamole/api/tokens/${encodeURIComponent(guacToken)}`, { method: 'DELETE' }).catch(() => null);
        }
        localStorage.removeItem('hv_token');
        localStorage.removeItem('hv_user');
        localStorage.removeItem('hv_guac_token');
        localStorage.removeItem('GUAC_AUTH');
        delete axios.defaults.headers.common['Authorization'];
        setTempPassword('');
        setAuthUser(null);
    };

    useEffect(() => {
        const token = localStorage.getItem('hv_token');
        if (token) axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    }, []);

    // ── Sesi habis: beri tahu user lalu lempar ke halaman login ──
    useEffect(() => {
        const onExpired = () => {
            setLoginNotice(t('app.sessionExpired'));
            handleLogout();
        };
        window.addEventListener('hv:session-expired', onExpired);
        return () => window.removeEventListener('hv:session-expired', onExpired);
    }, [t]);

    // Backend menolak dengan "wajib ganti password" (mis. sesi dari sebelum password direset).
    useEffect(() => {
        const onMustChange = () => setAuthUser(u => (u ? { ...u, must_change_password: true } : u));
        window.addEventListener('hv:must-change-password', onMustChange);
        return () => window.removeEventListener('hv:must-change-password', onMustChange);
    }, []);

    // Admin: jumlah permintaan "Lupa password?" yang belum ditangani, ditampilkan di tab Users.
    const seesPwHelp = ['superadmin', 'sysadmin'].includes(authUser?.role) && !authUser?.must_change_password;
    useEffect(() => {
        if (!seesPwHelp) return undefined;
        const load = () => fetchPasswordHelp().then(r => setPwHelpCount(r.length)).catch(() => {});
        load();
        const id = setInterval(load, 120000);
        window.addEventListener('hv:password-help-changed', load);
        return () => { clearInterval(id); window.removeEventListener('hv:password-help-changed', load); };
    }, [seesPwHelp]);

    // Pengumuman, kategori tiket, dan nilai bawaan dimuat setelah login, lalu diperbarui tiap 5 menit.
    useEffect(() => {
        if (!authUser) return undefined;
        loadSysConfig();
        const id = setInterval(loadSysConfig, 300000);
        return () => clearInterval(id);
    }, [authUser]);

    // Auth gate
    if (!authUser) return <LoginPage onLogin={handleLogin} notice={loginNotice} />;
    // Halaman khusus admin/superadmin yang dibuka langsung lewat alamatnya oleh akun yang tidak berhak.
    const isAdminRole = ['superadmin', 'sysadmin'].includes(authUser.role);
    const forbiddenNeed = activeTab === 'system' && authUser.role !== 'superadmin' ? 'superadmin'
        : ['users', 'groups', 'admin', 'status', 'instances'].includes(activeTab) && !isAdminRole ? 'admin' : null;

    if (authUser.must_change_password) {
        return <ForcePasswordChange user={authUser} knownPassword={tempPassword} onLogout={handleLogout}
            onDone={(user, token) => handleLogin(user, token)} />;
    }

    return (
        <div style={{ minHeight: '100vh', background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>

            {/* ── Topbar ── */}
            <div className="ccd-topbar" style={{ background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)', padding: '0 20px', height: 46, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, position: 'sticky', top: 0, zIndex: 100 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                    <BrandLogo size={26} />
                    <span className="ccd-hide-mobile" style={{ fontWeight: 600, fontSize: 14, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{branding.name}</span>
                    <span className="ccd-show-mobile" style={{ fontWeight: 600, fontSize: 14, whiteSpace: 'nowrap' }}>{branding.short_name}</span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                    <Clock />

                    {/* About / Tentang Sistem */}
                    <button onClick={() => setShowAbout(true)} title={t('header.about')}
                        style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, fontFamily: 'var(--fmono)', background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer' }}>
                        <span className="ccd-hide-mobile"> {t('header.about')}</span>
                    </button>

                    <LanguageToggle />

                    {/* Theme toggle */}
                    <ThemeToggle />

                    {/* User info + profile */}
                    <button
                        onClick={() => setShowProfile(true)}
                        title={t('app.profile')}
                        style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', cursor: 'pointer', minWidth: 0 }}
                    >
                        <span className="ccd-hide-mobile" style={{ fontSize: 11, color: 'var(--text2)', fontFamily: 'var(--fmono)', whiteSpace: 'nowrap' }}>
                            {authUser?.full_name || authUser?.username}
                        </span>
                        <span style={{
                            fontSize: 9, padding: '1px 6px', borderRadius: 4,
                            background: (ROLE_COLOR[authUser?.role] || 'var(--text3)') + '22',
                            color: ROLE_COLOR[authUser?.role] || 'var(--text3)'
                        }}>
                            {t(`role.${authUser?.role}`)}
                        </span>
                    </button>
                    <button onClick={handleLogout} title={t('common.logout')} aria-label={t('common.logout')}
                        style={{ width: 28, height: 28, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text3)', cursor: 'pointer', fontSize: 13 }}>
                        ⏻
                    </button>
                </div>
            </div>

            {/* ── Navbar tabs ── */}
            <div className="ccd-navbar" style={{ background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)', padding: '0 20px', display: 'flex', alignItems: 'center', gap: 2, position: 'sticky', top: 46, zIndex: 99 }}>
                {(isRestrictedStudent ? [
                    { id: 'requests', label: t('nav.myRequests') },
                ] : [
                    { id: 'servers',  label: t('nav.servers') },
                    { id: 'topology', label: t('nav.topology') },
                    { id: 'openweb',  label: t('nav.openweb') },
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'users', label: t('nav.users'), badge: seesPwHelp ? pwHelpCount : 0 }]
                        : []),
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'groups', label: t('nav.groups') }]
                        : []),
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'admin', label: t('nav.admin') }]
                        : []),
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'status', label: t('nav.status') }]
                        : []),
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'instances', label: t('nav.instances') }]
                        : []),
                    ...(authUser?.role === 'superadmin'
                        ? [{ id: 'system', label: t('nav.system') }]
                        : []),
                    { id: 'tickets',  label: ['superadmin', 'sysadmin'].includes(authUser?.role) ? t('nav.helpdesk') : t('nav.tickets') },
                    { id: 'requests', label: ['superadmin', 'sysadmin'].includes(authUser?.role) ? t('nav.infra') : t('nav.myRequests') },
                ]).map(tab => (
                    <button key={tab.id} onClick={() => setTab(tab.id)} className="ccd-tab" data-active={activeTab === tab.id ? 'true' : undefined}
                        style={{ whiteSpace: 'nowrap', flexShrink: 0, padding: '8px 16px', fontSize: 12, cursor: 'pointer', background: 'transparent', border: 'none', borderBottom: `2px solid ${activeTab === tab.id ? 'var(--cyan)' : 'transparent'}`, color: activeTab === tab.id ? 'var(--cyan)' : 'var(--text3)', fontWeight: activeTab === tab.id ? 600 : 400, transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>{tab.icon}</span> {tab.label}
                        {tab.badge > 0 && (
                            <span title={t('users.pwHelpBadge')} style={{ minWidth: 16, height: 16, padding: '0 4px', borderRadius: 8, background: 'var(--red)', color: '#fff', fontSize: 10, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>{tab.badge}</span>
                        )}
                    </button>
                ))}
            </div>

            <div style={{ flex: 1 }}>

            {sysConfig.announcement && (
                <div style={{ maxWidth: 1400, margin: '12px auto 0', padding: '0 20px' }}>
                    <AnnouncementBanner announcement={sysConfig.announcement} />
                </div>
            )}

            {forbiddenNeed && <ForbiddenPage need={forbiddenNeed} role={authUser.role} onBack={() => setTab('servers')} />}

            {/* ── Servers Tab (Proxmox) ── */}
            {activeTab === 'servers' && (
                <ProxmoxPage currentUser={authUser} />
            )}

            {/* ── Topology Tab (Proxmox) ── */}
            {activeTab === 'topology' && (
                <ProxmoxTopology currentUser={authUser} />
            )}

            {/* ── Open Web (browser iframe) ── */}
            {activeTab === 'openweb' && (
                <OpenWebPage />
            )}

            {/* ── Users Tab ── */}
            {activeTab === 'users' && isAdminRole && (
                <UsersPage currentUser={authUser} />
            )}

            {/* ── Groups Tab ── */}
            {activeTab === 'groups' && ['superadmin', 'sysadmin'].includes(authUser?.role) && (
                <div style={{ maxWidth: 1400, margin: '0 auto', padding: '16px 20px', boxSizing: 'border-box' }}>
                    <GroupsPage />
                </div>
            )}

            {/* ── Admin: Audit & Remote Monitoring ── */}
            {activeTab === 'admin' && ['superadmin', 'sysadmin'].includes(authUser?.role) && (
                <AdminPanel currentUser={authUser} />
            )}

            {/* ── Helpdesk / Tickets ── */}
            {activeTab === 'tickets' && (
                <TicketsPage currentUser={authUser} />
            )}

            {/* ── Infrastructure Requests ── */}
            {activeTab === 'requests' && (
                <InfraRequestsPage currentUser={authUser} />
            )}

            {/* ── Status / Health ── */}
            {activeTab === 'status' && ['superadmin', 'sysadmin'].includes(authUser?.role) && (
                <StatusPage />
            )}

            {/* ── Pengaturan Sistem (superadmin) ── */}
            {activeTab === 'system' && authUser?.role === 'superadmin' && (
                <SystemSettingsPage />
            )}

            {/* ── Proxmox Instances ── */}
            {activeTab === 'instances' && ['superadmin', 'sysadmin'].includes(authUser?.role) && (
                <div style={{ padding: '16px 20px', maxWidth: 700, margin: '0 auto' }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 14 }}>
                        {t('app.instancesTitle')}
                    </div>
                    <ProxmoxInstancesPanel />
                </div>
            )}
            </div>

            {/* ── Global footer (academic copyright / attribution) ── */}
            <AppFooter />

            {showAbout && <AboutModal onClose={() => setShowAbout(false)} />}
            {showProfile && <ProfileModal user={authUser} onClose={() => setShowProfile(false)} />}

            <style>{`
                @keyframes blink{0%,100%{opacity:1}50%{opacity:0.5}}
            `}</style>
        </div>
    );
}
