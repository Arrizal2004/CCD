import { useState, useEffect } from 'react';
import LoginPage from './pages/LoginPage';
import UsersPage from './pages/UserPage';
import { useNavigate, useLocation } from 'react-router-dom';
import { resetSessionExpired, logoutApi } from './api';
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
    const [theme, setTheme] = useState(() => localStorage.getItem('hv-theme') || 'dark');
    const [loginNotice, setLoginNotice] = useState('');
    const [showAbout, setShowAbout] = useState(false);
    const [showProfile, setShowProfile] = useState(false);

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
        location.pathname === '/instances' ? 'instances' : 'servers';
    const setTab = (tab) => navigate(`/${tab}`);

    // Student yang belum verified hanya boleh akses /requests
    const isRestrictedStudent = authUser?.role === 'student' && authUser?.is_verified === false;
    useEffect(() => {
        if (isRestrictedStudent && activeTab !== 'requests') navigate('/requests', { replace: true });
    }, [isRestrictedStudent, activeTab, navigate]);

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : '');
        document.documentElement.style.background = theme === 'light' ? '#f0f4f8' : '#0b0f1a';
        localStorage.setItem('hv-theme', theme);
    }, [theme]);

    const handleLogin = (user, token) => {
        axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
        resetSessionExpired();   // izinkan deteksi sesi-habis berikutnya
        setLoginNotice('');
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
        setAuthUser(null);
    };

    useEffect(() => {
        const token = localStorage.getItem('hv_token');
        if (token) axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    }, []);

    // ── Sesi habis: beri tahu user lalu lempar ke halaman login ──
    useEffect(() => {
        const onExpired = () => {
            setLoginNotice('Sesi kamu telah berakhir. Silakan login kembali.');
            handleLogout();
        };
        window.addEventListener('hv:session-expired', onExpired);
        return () => window.removeEventListener('hv:session-expired', onExpired);
    }, []);

    // Auth gate
    if (!authUser) return <LoginPage onLogin={handleLogin} notice={loginNotice} />;

    return (
        <div style={{ minHeight: '100vh', background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>

            {/* ── Topbar ── */}
            <div style={{ background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)', padding: '0 20px', height: 46, display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'sticky', top: 0, zIndex: 100 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{ width: 26, height: 26, borderRadius: 6, flexShrink: 0, background: 'linear-gradient(135deg,var(--cyan),var(--blue))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700, color: '#000' }}>C</div>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>Campus Cloud Dashboard</span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {/* About / Tentang Sistem */}
                    <button onClick={() => setShowAbout(true)} title="Tentang Sistem"
                        style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, fontFamily: 'var(--fmono)', background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer' }}>
                        ℹ️ Tentang Sistem
                    </button>

                    {/* Theme toggle */}
                    <button onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')} style={{ width: 30, height: 30, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text2)', cursor: 'pointer' }}>
                        {theme === 'dark' ? '☀️' : '🌙'}
                    </button>

                    {/* User info + profile */}
                    <button
                        onClick={() => setShowProfile(true)}
                        title="Profil & Ganti Password"
                        style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 6, background: 'var(--bg-hover)', border: '1px solid var(--border)', cursor: 'pointer' }}
                    >
                        <span style={{ fontSize: 11, color: 'var(--text2)', fontFamily: 'var(--fmono)' }}>
                            {authUser?.full_name || authUser?.username}
                        </span>
                        <span style={{
                            fontSize: 9, padding: '1px 6px', borderRadius: 4,
                            background: (ROLE_COLOR[authUser?.role] || 'var(--text3)') + '22',
                            color: ROLE_COLOR[authUser?.role] || 'var(--text3)'
                        }}>
                            {authUser?.role}
                        </span>
                    </button>
                    <button onClick={handleLogout} title="Logout"
                        style={{ width: 28, height: 28, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text3)', cursor: 'pointer', fontSize: 13 }}>
                        ⏻
                    </button>
                </div>
            </div>

            {/* ── Navbar tabs ── */}
            <div style={{ background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)', padding: '0 20px', display: 'flex', alignItems: 'center', gap: 2, position: 'sticky', top: 46, zIndex: 99 }}>
                {(isRestrictedStudent ? [
                    { id: 'requests', label: 'My Requests' },
                ] : [
                    { id: 'servers',  label: 'Servers' },
                    { id: 'topology', label: 'Topology' },
                    { id: 'openweb',  label: 'Open Web' },
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'users', label: 'Users' }]
                        : []),
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'groups', label: 'Groups' }]
                        : []),
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'admin', label: 'Audit & Remote' }]
                        : []),
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'status', label: 'Status' }]
                        : []),
                    ...(['superadmin', 'sysadmin'].includes(authUser?.role)
                        ? [{ id: 'instances', label: 'Integrations' }]
                        : []),
                    { id: 'tickets',  label: ['superadmin', 'sysadmin'].includes(authUser?.role) ? 'Helpdesk' : 'Tickets' },
                    { id: 'requests', label: ['superadmin', 'sysadmin'].includes(authUser?.role) ? 'Infra Requests' : 'My Requests' },
                ]).map(tab => (
                    <button key={tab.id} onClick={() => setTab(tab.id)}
                        style={{ padding: '8px 16px', fontSize: 12, cursor: 'pointer', background: 'transparent', border: 'none', borderBottom: `2px solid ${activeTab === tab.id ? 'var(--cyan)' : 'transparent'}`, color: activeTab === tab.id ? 'var(--cyan)' : 'var(--text3)', fontWeight: activeTab === tab.id ? 600 : 400, transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>{tab.icon}</span> {tab.label}
                    </button>
                ))}
            </div>

            <div style={{ flex: 1 }}>

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
            {activeTab === 'users' && (
                <UsersPage currentUser={authUser} />
            )}

            {/* ── Groups Tab ── */}
            {activeTab === 'groups' && ['superadmin', 'sysadmin'].includes(authUser?.role) && (
                <div style={{ padding: '16px 0' }}>
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

            {/* ── Proxmox Instances ── */}
            {activeTab === 'instances' && ['superadmin', 'sysadmin'].includes(authUser?.role) && (
                <div style={{ padding: '16px 20px', maxWidth: 700, margin: '0 auto' }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 14 }}>
                        Proxmox Instances
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
