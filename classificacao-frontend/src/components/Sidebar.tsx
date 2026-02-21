import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { clearAuth, getUsuario } from '@/lib/auth';
import {
    PencilLine,
    CheckCircle2,
    BarChart3,
    LogOut,
    User as UserIcon
} from 'lucide-react';
import styles from './Sidebar.module.css';
import { useEffect, useState } from 'react';

export default function Sidebar() {
    const pathname = usePathname();
    const router = useRouter();
    const [usuario, setUsuario] = useState<any>(null);

    useEffect(() => {
        setUsuario(getUsuario());
    }, []);

    const handleLogout = () => {
        clearAuth();
        router.push('/login');
    };

    const menuItems = [
        { name: 'Classificar', path: '/classificar', icon: <PencilLine size={20} /> },
        { name: 'Verificar', path: '/verificar', icon: <CheckCircle2 size={20} /> },
        { name: 'Estatísticas', path: '/stats', icon: <BarChart3 size={20} /> },
    ];

    if (!usuario) return null;

    return (
        <aside className={styles.sidebar}>
            <div className={styles.logo}>
                <h1>Classificador</h1>
                <p>Sistema de Apoio</p>
            </div>

            <div className={styles.user}>
                <div className={styles.avatar}>
                    <UserIcon size={20} />
                </div>
                <div className={styles.userInfo}>
                    <h3>{usuario.nome}</h3>
                    <span>{usuario.is_admin ? 'Admin' : usuario.disciplina}</span>
                </div>
            </div>

            <nav className={styles.nav}>
                {menuItems.map((item) => (
                    <Link
                        key={item.path}
                        href={item.path}
                        className={`${styles.navLink} ${pathname === item.path ? styles.active : ''}`}
                    >
                        <span className={styles.icon}>{item.icon}</span>
                        {item.name}
                    </Link>
                ))}
            </nav>

            <button onClick={handleLogout} className={styles.logout}>
                <LogOut size={20} />
                Sair
            </button>
        </aside>
    );
}
