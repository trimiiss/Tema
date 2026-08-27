"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, CalendarHeart } from "lucide-react";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) { setError(error.message); setLoading(false); return; }
    router.replace("/dashboard");
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-primary-50 to-blue-100">
      <div className="w-full max-w-md">
        {/* This page is reached from "Staff Login" on the public site and has no
            header of its own, so without this there is no way back to the
            clinic site short of the browser's back button. */}
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-primary-700 transition-colors mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Trim&apos;s Clinic
        </Link>

        <div className="bg-white rounded-2xl shadow-xl p-8">
          <div className="text-center mb-8">
            <div className="w-16 h-16 bg-primary-600 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <span className="text-white text-2xl font-bold">T</span>
            </div>
            <h1 className="text-2xl font-bold text-gray-900">Clinic Admin System</h1>
            <p className="text-gray-500 mt-1">Staff sign-in — for clinic accounts only</p>
          </div>

          <form onSubmit={handleLogin} className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none"
                placeholder="you@clinic.demo"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none"
                placeholder="••••••••"
                required
              />
            </div>
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-primary-600 hover:bg-primary-700 text-white font-semibold py-2.5 rounded-lg transition disabled:opacity-50"
            >
              {loading ? "Signing in..." : "Sign In"}
            </button>
          </form>

          {/* A patient who clicked the wrong button has no account here and never
              will — send them where they were actually going. */}
          <div className="mt-6 pt-5 border-t border-gray-100 text-center">
            <p className="text-sm text-gray-500">Not staff? You don&apos;t need an account to book.</p>
            <Link
              href="/book"
              className="mt-2 inline-flex items-center gap-1.5 text-sm font-semibold text-primary-600 hover:text-primary-700 transition-colors"
            >
              <CalendarHeart className="w-4 h-4" />
              Book an appointment
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
