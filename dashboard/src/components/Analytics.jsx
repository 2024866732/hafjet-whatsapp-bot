/**
 * Analytics — Sprint 1 Feature 3 (Reply.la style)
 * Cards + Recharts line chart + Fallback/Escalation
 */
import { useState, useEffect } from 'react';
import {
  fetchAnalyticsOverview,
  fetchAnalyticsChart,
} from '../api/api';
import {
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

function CircularProgress({ value, size = 120, strokeWidth = 8 }) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (value / 100) * circumference;
  const color = value >= 80 ? '#00d563' : value >= 50 ? '#f59e0b' : '#ef4444';
  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#2a2d3a"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
        />
      </svg>
      <span className="absolute text-white font-bold text-lg">{value.toFixed(1)}%</span>
    </div>
  );
}

export default function Analytics() {
  const [overview, setOverview] = useState(null);
  const [chartDays, setChartDays] = useState(7);
  const [chartData, setChartData] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadOverview = async () => {
    try {
      const data = await fetchAnalyticsOverview();
      setOverview(data);
    } catch (e) {
      console.error('Analytics overview failed:', e);
    }
  };

  const loadChart = async (days) => {
    try {
      const data = await fetchAnalyticsChart(days);
      setChartData(
        (data.labels || []).map((label, i) => ({
          label,
          messages_in: data.messages_in?.[i] || 0,
          messages_out: data.messages_out?.[i] || 0,
        }))
      );
    } catch (e) {
      console.error('Analytics chart failed:', e);
    }
  };

  useEffect(() => {
    const init = async () => {
      setLoading(true);
      await Promise.all([loadOverview(), loadChart(chartDays)]);
      setLoading(false);
    };
    init();
    const interval = setInterval(() => {
      loadOverview();
      loadChart(chartDays);
    }, 30000);
    return () => clearInterval(interval);
  }, [chartDays]);

  if (loading) {
    return (
      <div className="flex-1 bg-[#0f1117] p-8 flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!overview) {
    return (
      <div className="flex-1 bg-[#0f1117] p-8 flex items-center justify-center">
        <p className="text-gray-500">Gagal muat data analytics</p>
      </div>
    );
  }

  const aiReplyRate = overview.ai_reply_rate || 0;
  const fallbackRate = 100 - aiReplyRate;
  const changePct = overview.messages_change_pct || 0;
  const isUp = changePct >= 0;

  return (
    <div className="flex-1 bg-[#0f1117] p-6 overflow-y-auto">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h2 className="text-white text-xl font-bold">Analytics</h2>
          <p className="text-gray-400 text-sm">Statistik prestasi bot • Auto-refresh 30s</p>
        </div>

        {/* Stat Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <div className="bg-[#1a1d27] rounded-xl p-5 border border-gray-800">
            <p className="text-gray-400 text-xs mb-1">Total Perbualan</p>
            <p className="text-white text-2xl font-bold">{overview.total_conversations || 0}</p>
            <p className="text-emerald-400 text-xs mt-1">
              +{overview.new_last_7_days || 0} baru dalam 7 hari
            </p>
          </div>

          <div className="bg-[#1a1d27] rounded-xl p-5 border border-gray-800">
            <p className="text-gray-400 text-xs mb-1">Mesej Bulan Ini</p>
            <p className="text-white text-2xl font-bold">{overview.messages_this_month || 0}</p>
            <p className={`text-xs mt-1 ${isUp ? 'text-emerald-400' : 'text-red-400'}`}>
              {isUp ? '▲' : '▼'} {Math.abs(changePct).toFixed(1)}% vs bulan lepas
            </p>
          </div>

          <div className="bg-[#1a1d27] rounded-xl p-5 border border-gray-800">
            <p className="text-gray-400 text-xs mb-1">AI Reply Rate</p>
            <p className="text-white text-2xl font-bold">{aiReplyRate.toFixed(1)}%</p>
            <p className={`text-xs mt-1 ${aiReplyRate >= 80 ? 'text-emerald-400' : 'text-amber-400'}`}>
              {aiReplyRate >= 80 ? '✅ AI handle baik' : '⚠️ Ramai perlu staff'}
            </p>
          </div>

          <div className="bg-[#1a1d27] rounded-xl p-5 border border-gray-800">
            <p className="text-gray-400 text-xs mb-1">Mesej Hari Ini</p>
            <p className="text-white text-2xl font-bold">{overview.today_messages || 0}</p>
            <p className="text-gray-500 text-xs mt-1">mesej masuk hari ini</p>
          </div>
        </div>

        {/* Line Chart */}
        <div className="bg-[#1a1d27] rounded-xl p-6 mb-6 border border-gray-800">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
            <div>
              <h3 className="text-white font-semibold">Mesej Masuk vs Keluar</h3>
              <p className="text-gray-400 text-sm">Trend {chartDays} hari lepas</p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setChartDays(7)}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                  chartDays === 7
                    ? 'bg-[#00d563] text-black'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                7 Hari
              </button>
              <button
                onClick={() => setChartDays(30)}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                  chartDays === 30
                    ? 'bg-[#00d563] text-black'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                30 Hari
              </button>
            </div>
          </div>

          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
              <XAxis dataKey="label" stroke="#6b7280" tick={{ fill: '#6b7280', fontSize: 12 }} />
              <YAxis stroke="#6b7280" tick={{ fill: '#6b7280', fontSize: 12 }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1a1d27',
                  border: '1px solid #2a2d3a',
                  borderRadius: '8px',
                  color: '#fff',
                }}
                labelStyle={{ color: '#fff' }}
              />
              <Legend />
              <Line
                type="monotone"
                dataKey="messages_in"
                stroke="#00d563"
                strokeWidth={2}
                dot={{ fill: '#00d563', r: 4 }}
                name="Masuk"
              />
              <Line
                type="monotone"
                dataKey="messages_out"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={{ fill: '#3b82f6', r: 4 }}
                name="Keluar"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Bottom Row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Fallback Rate */}
          <div className="bg-[#1a1d27] rounded-xl p-6 border border-gray-800">
            <h3 className="text-gray-400 text-sm mb-4">Rate Fallback (Tanpa AI)</h3>
            <div className="flex items-center gap-4">
              <CircularProgress value={fallbackRate} />
              <p className="text-gray-400 text-sm leading-relaxed">
                {aiReplyRate >= 80
                  ? '✅ Kebanyakan mesej dihandle AI dengan baik.'
                  : '⚠️ Ramai pelanggan perlu staff intervensi.'}
              </p>
            </div>
          </div>

          {/* Escalation Count */}
          <div className="bg-[#1a1d27] rounded-xl p-6 border border-gray-800">
            <h3 className="text-gray-400 text-sm mb-2">Rate Escalation</h3>
            <p className="text-5xl font-bold text-white">{overview.escalation_count || 0}</p>
            <p className="text-gray-400 text-sm mt-2">kes yang perlu ditangani staff</p>
          </div>
        </div>
      </div>
    </div>
  );
}
