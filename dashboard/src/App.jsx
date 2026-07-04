/**
 * App — Main entry point
 */
import { useState, useEffect } from 'react';
import { fetchStaffMe, staffLogin } from './api/api';
import Layout from './components/Layout';
import Login from './components/Login';

export default function App() {
  const [isAuthed, setIsAuthed] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('staff_token');
    if (!token) {
      setIsAuthed(false);
      setLoading(false);
      return;
    }
    fetchStaffMe()
      .then(() => setIsAuthed(true))
      .catch(() => {
        localStorage.removeItem('staff_token');
        setIsAuthed(false);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0f1117] flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  if (!isAuthed) {
    return <Login onLogin={() => setIsAuthed(true)} />;
  }

  return <Layout onLogout={() => { localStorage.removeItem('staff_token'); setIsAuthed(false); }} />;
}
