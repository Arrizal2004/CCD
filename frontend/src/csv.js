// Parser CSV kecil untuk impor akun. Mendukung pemisah koma atau titik koma (Excel berbahasa
// Indonesia menyimpan CSV dengan titik koma), tanda kutip, dan BOM. Baris pertama adalah header.

const ALIASES = {
    username: 'username', user: 'username',
    full_name: 'full_name', nama: 'full_name', nama_lengkap: 'full_name', name: 'full_name',
    email: 'email',
    password: 'password', kata_sandi: 'password',
    role: 'role', peran: 'role',
    expires_at: 'expires_at', masa_berlaku: 'expires_at', berlaku_sampai: 'expires_at',
    group: 'group', grup: 'group', kelas: 'group',
};

function splitLine(line, sep) {
    const out = [];
    let cur = '', quoted = false;
    for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (quoted) {
            if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
            else if (ch === '"') quoted = false;
            else cur += ch;
        } else if (ch === '"') quoted = true;
        else if (ch === sep) { out.push(cur); cur = ''; }
        else cur += ch;
    }
    out.push(cur);
    return out.map(s => s.trim());
}

export function parseCsv(text) {
    const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(l => l.trim() !== '');
    if (lines.length === 0) return { rows: [], unknown: [] };
    const sep = (lines[0].match(/;/g) || []).length > (lines[0].match(/,/g) || []).length ? ';' : ',';
    const header = splitLine(lines[0], sep).map(h => h.toLowerCase().replace(/\s+/g, '_'));
    const keys = header.map(h => ALIASES[h] || null);
    const unknown = header.filter((h, i) => !keys[i]);
    const rows = lines.slice(1).map(line => {
        const cells = splitLine(line, sep);
        const row = {};
        keys.forEach((k, i) => { if (k && cells[i]) row[k] = cells[i]; });
        return row;
    });
    return { rows, unknown };
}

export const CSV_TEMPLATE = 'username,full_name,email,password,role,expires_at,group\n'
    + 'siswa01,Budi Santoso,budi@sekolah.sch.id,,student,2027-06-30,XII-TKJ-1\n'
    + 'siswa02,Siti Aminah,siti@sekolah.sch.id,,student,2027-06-30,XII-TKJ-1\n';

// Daftar username dan password (yang dibuat otomatis) untuk diunduh setelah impor.
export function credentialsCsv(creds) {
    return 'username,password\n' + creds.map(c => `${c.username},${c.password}`).join('\n') + '\n';
}
