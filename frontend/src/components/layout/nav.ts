import {
  Activity,
  Bell,
  Bot,
  BrainCircuit,
  Briefcase,
  FlaskConical,
  Funnel,
  LayoutDashboard,
  LayoutGrid,
  Newspaper,
  NotebookPen,
  PlugZap,
  Search,
  Settings,
  ShieldAlert,
  Star,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    label: "Markets",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/markets", label: "Live Markets", icon: Activity },
      { href: "/heatmap", label: "Market Heatmap", icon: LayoutGrid },
      { href: "/stocks", label: "Stock Explorer", icon: Search },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/ai", label: "AI Research", icon: Bot },
      { href: "/ml", label: "ML Predictions", icon: BrainCircuit },
      { href: "/screener", label: "Stock Screener", icon: Funnel },
      { href: "/news", label: "News Intelligence", icon: Newspaper },
    ],
  },
  {
    label: "Portfolio",
    items: [
      { href: "/watchlists", label: "Watchlists", icon: Star },
      { href: "/portfolios", label: "Portfolio Manager", icon: Briefcase },
      { href: "/journal", label: "Trading Journal", icon: NotebookPen },
      { href: "/backtest", label: "Strategy Backtesting", icon: FlaskConical },
      { href: "/alerts", label: "Alerts", icon: Bell },
      { href: "/risk", label: "Risk Analytics", icon: ShieldAlert },
    ],
  },
  {
    label: "Administration",
    items: [
      { href: "/settings/providers", label: "Data Provider Settings", icon: PlugZap, adminOnly: true },
      { href: "/admin", label: "System Administration", icon: Settings },
    ],
  },
];

export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
