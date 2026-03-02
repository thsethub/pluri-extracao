import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { clearAuth, getUsuario, type Usuario } from '@/lib/auth';
import {
    PencilLine,
    CheckCircle2,
    BarChart3,
    LogOut,
    Clock,
    User as UserIcon,
    Cpu,
    Search
} from 'lucide-react';
import styles from './Sidebar.module.css';
import { type ReactNode } from 'react';

type MenuItem = {
    name: string;
    path: string;
    icon: ReactNode;
};

export default function Sidebar() {
    const pathname = usePathname();
    const router = useRouter();
    const usuario: Usuario | null = getUsuario();

    const handleLogout = () => {
        clearAuth();
        router.push('/login');
    };

    const plataformaItems: MenuItem[] = [
        { name: 'Classificar', path: '/classificar', icon: <PencilLine size={20} /> },
        { name: 'Verificar', path: '/verificar', icon: <CheckCircle2 size={20} /> },
        { name: 'Pendentes', path: '/pendentes', icon: <Clock size={20} /> },
        { name: 'Estatisticas', path: '/stats', icon: <BarChart3 size={20} /> },
    ];

    const sistemaItems: MenuItem[] = usuario?.is_admin
        ? [
            { name: 'Agente IA', path: '/agente-ia', icon: <Cpu size={20} /> },
            { name: 'Consulta', path: '/consulta', icon: <Search size={20} /> },
        ]
        : [];

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
                <div className={styles.section}>
                    <p className={styles.sectionTitle}>Plataforma</p>
                    <div className={styles.sectionLinks}>
                        {plataformaItems.map((item) => (
                            <Link
                                key={item.path}
                                href={item.path}
                                className={`${styles.navLink} ${pathname === item.path ? styles.active : ''}`}
                            >
                                <span className={styles.icon}>{item.icon}</span>
                                {item.name}
                            </Link>
                        ))}
                    </div>
                </div>

                {sistemaItems.length > 0 && (
                    <div className={styles.section}>
                        <p className={styles.sectionTitle}>Sistema</p>
                        <div className={styles.sectionLinks}>
                            {sistemaItems.map((item) => (
                                <Link
                                    key={item.path}
                                    href={item.path}
                                    className={`${styles.navLink} ${pathname === item.path ? styles.active : ''}`}
                                >
                                    <span className={styles.icon}>{item.icon}</span>
                                    {item.name}
                                </Link>
                            ))}
                        </div>
                    </div>
                )}
            </nav>

            <button onClick={handleLogout} className={styles.logout}>
                <LogOut size={20} />
                Sair
            </button>
        </aside>
    );
}
