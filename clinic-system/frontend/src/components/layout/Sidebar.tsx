"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { usersApi } from "@/lib/api";

const NAV = [
  { href: "/dashboard",    label: "Dashboard",     icon: "🏠" },
  { href: "/appointments", label: "Appointments",  icon: "📅" },
  { href: "/patients",     label: "Patients",      icon: "👤" },
  { href: "/staff",        label: "Staff",         icon: "🩺", roles: ["admin"] },
  { href: "/documents",    label: "Documents",     icon: "📄" },
  { href: "/reports",      label: "Reports",       icon: "📊" },
  { href: "/agent-chat",   label: "Agent Chat",    icon: "🤖", roles: ["admin", "receptionist"] },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<{ full_name: string | null; email: string; roles: string[] } | null>(null);

  useEffect(() => {
    usersApi.me().then(setMe).catch(() => setMe(null));
  }, []);

  async function logout() {
    await supabase.auth.signOut();
    router.replace("/login");
  }

  return (
    <aside className="w-60 min-h-screen bg-white border-r border-gray-200 flex flex-col">
      <div className="px-6 py-5 border-b border-gray-100">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-primary-600 rounded-lg flex items-center justify-center">
            <span className="text-white font-bold text-sm">C</span>
          </div>
          <div>
            <p className="text-sm font-bold text-gray-900">Clinic System</p>
            <p className="text-xs text-gray-400">Multi-Agent System</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV.filter(n => !n.roles || n.roles.some(r => me?.roles?.includes(r))).map(n => {
          const active = pathname.startsWith(n.href);
          return (
            <Link
              key={n.href}
              href={n.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition
                ${active
                  ? "bg-primary-50 text-primary-700"
                  : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`}
            >
              <span>{n.icon}</span>
              {n.label}
            </Link>
          );
        })}
      </nav>

      <div className="px-3 py-4 border-t border-gray-100 space-y-3">
        {me && (
          <div className="px-3">
            <p className="text-sm font-semibold text-gray-900 truncate">{me.full_name || me.email}</p>
            <p className="text-xs text-gray-400 truncate">
              {me.email}{me.roles.length > 0 ? ` · ${me.roles.join(", ")}` : ""}
            </p>
          </div>
        )}
        <button
          onClick={logout}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-gray-600 hover:bg-red-50 hover:text-red-600 transition"
        >
          <span>🚪</span> Sign Out
        </button>
      </div>
    </aside>
  );
}
