/**
 * Login — Staff agent login page (JWT)
 */
import { useState, useEffect } from 'react';
import { jwtDecode } from 'jwt-decode';

const API_BASE = import.meta.env.VITE_API_BASE || '';

export default function Login({ onLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('staff_token');
    if (token) {
      try {
        const decoded = jwtDecode(token);
        const exp = decoded.exp * 1000;
        if (Date.now() < exp) {
          onLogin?.();
          return;
        }
      } catch {
        localStorage.removeItem('staff_token');
      }
    }
  }, [onLogin]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API_BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      console.debug('[Login] response status', res.status, data);
      if (!res.ok) {
        setError(data.detail || data.error || 'Login gagal');
        return;
      }
      localStorage.setItem('staff_token', data.access_token);
      localStorage.setItem('staff_info', JSON.stringify(data.staff));
      onLogin?.();
    } catch (e) {
      console.debug('[Login] network error', e);
      setError('Ralat jaringan');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center p-4">
      <div className="bg-[#1a1d27] rounded-xl p-8 w-full max-w-md border border-gray-800">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 bg-[#00d563] rounded-lg flex items-center justify-center">
            <span className="text-white font-bold text-lg">H</span>
          </div>
          <div>
            <h1 className="text-white font-semibold text-xl">HAFJET Agent</h1>
            <p className="text-gray-400 text-xs">Multi-Agent Inbox</p>
          </div>
        </div>

        {error && (
          <div className="bg-red-900/30 border border-red-500/50 text-red-400 rounded-lg p-3 mb-4 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-gray-400 text-sm mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-2.5 text-white text-sm placeholder-gray-500 focus:outline-none focus:border-[#00d563]"
              placeholder="nama@hafjet.com"
            />
          </div>
          <div>
            <label className="block text-gray-400 text-sm mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-2.5 text-white text-sm placeholder-gray-500 focus:outline-none focus:border-[#00d563]"
              placeholder="••••••••"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-[#00d563] hover:bg-[#00c255] disabled:bg-gray-600 text-black font-medium py-2.5 rounded-lg text-sm transition"
          >
            {loading ? 'Logging in...' : 'Log Masuk'}
          </button>
        </form>

        <p className="text-gray-500 text-xs text-center mt-6">
          Hanya untuk kakitangan HAFJET sahaja.
        </p>
      </div>
    </div>
  );
}
