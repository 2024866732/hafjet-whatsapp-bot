/**
 * Analytics — Sprint v2.2.0
 * Summary cards + dual charts + agent perf table + CSV export
 */
import { useState, useEffect, useCallback } from 'react';
import {
  fetchAnalyticsOverview,
  fetchAnalyticsTimeseries,
  fetchAgents,
  downloadAnalyticsCSV,
} from '../api/api';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '-';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function Card({ label, value, sub, color = 'text-white' }) {
  return (
    <div className="bg-[#1a1d27] rounded-xl p-5 border border-gray-800">
      <p className="text-gray-400 text-xs mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value ?? '-'}</p>
      {sub && <p className="text-gray-500 text-xs mt-1">{sub}</p>}
    </div>
  );
}

function LoadingSpinner() {
  return (
    <div className="flex-1 bg-[#0f1117] p-8 flex items-center justify-center">
      <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function EmptyState({ message = 'Tiada data tersedia' }) {
  return (
    <div className="bg-[#1a1d27] rounded-xl p-8 border border-gray-800 text-center">
      <p className="text-gray-500 text-sm">{message}</p>
    </div>
  );
}

export default function Analytics() {
  const [overview, setOverview] = useState(null);
  const [chartDays, setChartDays] = useState(7);
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [dateMode, setDateMode] = useState('7d'); // '7d' | '30d' | 'custom'
  const [chartData, setChartData] = useState([]);
  const [timeseries, setTimeseries] = useState(null);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  const getDateRange = useCallback(() => {
    if (dateMode === 'custom' && customStart && customEnd) {
      return { start: customStart, end: customEnd };
    }
    return {};
  }, [dateMode, customStart, customEnd]);

  const loadAll = useCallback(async () => {
    try {
      const days = dateMode === '30d' ? 30 : 7;
      const dateOpts = getDateRange();

      const [overviewData, timeseriesData, agentsData] = await Promise.all([
        fetchAnalyticsOverview(),
        fetchAnalyticsTimeseries({ days, ...dateOpts }),
        fetchAgents(dateOpts),
      ]);

      setOverview(overviewData);
      setTimeseries(timeseriesData);

      // Build chart data for line chart (in/out)
      setChartData(
        (timeseriesData.labels || []).map((label, i) => ({
          label,
          messages_in: timeseriesData.messages_in?.[i] || 0,
          messages_out: timeseriesData.messages_out?.[i] || 0,
          escalated: timeseriesData.escalated?.[i] || 0,
          resolved: timeseriesData.resolved?.[i] || 0,
          response_time: timeseriesData.response_times_sec?.[i] || 0,
        }))
      );

      setAgents(agentsData.agents || []);
    } catch (e) {
      console.error('Analytics load failed:', e);
    }
  }, [dateMode, getDateRange]);

  useEffect(() => {
    const init = async () => {
      setLoading(true);
      await loadAll();
      setLoading(false);
    };
    init();
    const interval = setInterval(() => loadAll(), 30000);
    return () => clearInterval(interval);
  }, [loadAll]);

  const handleExport = async () => {
    setExporting(true);
    try {
      const dateOpts = getDateRange();
      await downloadAnalyticsCSV({ exportType: 'overview', ...dateOpts });
    } catch (e) {
      console.error('CSV export failed:', e);
    } finally {
      setExporting(false);
    }
  };

  const handleDateMode = (mode) => {
    setDateMode(mode);
    if (mode === '7d') setChartDays(7);
    else if (mode === '30d') setChartDays(30);
  };

  if (loading) return <LoadingSpinner />;

  if (!overview) {
    return (
      <div className="flex-1 bg-[#0f1117] p-8 flex items-center justify-center">
        <p className="text-gray-500">Gagal muat data analytics</p>
      </div>
    );
  }

  const topRow = [
    { label: 'Total Perbualan', value: overview.total_conversations, sub: null },
    { label: 'Mesej Masuk', value: overview.inbound_total, sub: null },
    { label: 'Mesej Keluar', value: overview.outbound_total, sub: null },
    { label: 'Mesej Hari Ini', value: overview.today_messages, sub: 'hari ini' },
    { label: 'Eskalasi', value: overview.escalation_count, sub: 'kes perlu staff', color: 'text-amber-400' },
    { label: 'Selesai', value: overview.resolved_count, sub: 'kes selesai', color: 'text-emerald-400' },
    { label: 'Masa Respons Pertama', value: formatDuration(overview.avg_first_response_time_sec), sub: 'purata', color: 'text-blue-400' },
    { label: 'Masa Selesai', value: formatDuration(overview.avg_resolution_time_sec), sub: 'purata', color: 'text-purple-400' },
  ];

  return (
    <div className="flex-1 bg-[#0f1117] p-6 overflow-y-auto">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
          <div>
            <h2 className="text-white text-xl font-bold">Analytics</h2>
            <p className="text-gray-400 text-sm">Statistik prestasi bot • Auto-refresh 30s</p>
          </div>
          <div className="flex items-center gap-2">
            {/* Date range selector */}
            <div className="flex gap-1 bg-[#1a1d27] rounded-lg p-1 border border-gray-800">
              {['7d', '30d', 'custom'].map((m) => (
                <button
                  key={m}
                  onClick={() => handleDateMode(m)}
                  className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                    dateMode === m
                      ? 'bg-[#00d563] text-black'
                      : 'text-gray-400 hover:text-white'
                  }`}
                >
                  {m === '7d' ? '7 Hari' : m === '30d' ? '30 Hari' : 'Tempoh'}
                </button>
              ))}
            </div>
            {/* CSV export */}
            <button
              onClick={handleExport}
              disabled={exporting}
              className="px-3 py-1.5 rounded text-xs font-medium bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors disabled:opacity-50"
            >
              {exporting ? '...' : 'CSV'}
            </button>
          </div>
        </div>

        {/* Custom date inputs */}
        {dateMode === 'custom' && (
          <div className="flex items-center gap-2 mb-4">
            <input
              type="date"
              value={customStart}
              onChange={(e) => setCustomStart(e.target.value)}
              className="bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-1.5 text-white text-sm"
            />
            <span className="text-gray-500 text-xs">hingga</span>
            <input
              type="date"
              value={customEnd}
              onChange={(e) => setCustomEnd(e.target.value)}
              className="bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-1.5 text-white text-sm"
            />
            <button
              onClick={loadAll}
              className="px-3 py-1.5 rounded text-xs font-medium bg-[#00d563] text-black hover:bg-[#00c255]"
            >
              Guna
            </button>
          </div>
        )}

        {/* Summary Cards */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 mb-6">
          {topRow.map((card, i) => (
            <Card key={i} {...card} />
          ))}
        </div>

        {/* Line Chart — In/Out */}
        <div className="bg-[#1a1d27] rounded-xl p-6 mb-4 border border-gray-800">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-white font-semibold">Mesej Masuk vs Keluar</h3>
              <p className="text-gray-400 text-sm">
                Trend {dateMode === '7d' ? '7' : dateMode === '30d' ? '30' : ''} hari
              </p>
            </div>
          </div>
          {chartData.length > 0 && chartData.some((d) => d.messages_in > 0 || d.messages_out > 0) ? (
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
                <XAxis dataKey="label" stroke="#6b7280" tick={{ fill: '#6b7280', fontSize: 12 }} />
                <YAxis stroke="#6b7280" tick={{ fill: '#6b7280', fontSize: 12 }} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1a1d27', border: '1px solid #2a2d3a', borderRadius: '8px', color: '#fff' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Legend />
                <Line type="monotone" dataKey="messages_in" stroke="#00d563" strokeWidth={2} dot={{ fill: '#00d563', r: 3 }} name="Masuk" />
                <Line type="monotone" dataKey="messages_out" stroke="#3b82f6" strokeWidth={2} dot={{ fill: '#3b82f6', r: 3 }} name="Keluar" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState message="Tiada data mesej untuk tempoh ini" />
          )}
        </div>

        {/* Second chart — Escalated vs Resolved */}
        <div className="bg-[#1a1d27] rounded-xl p-6 mb-4 border border-gray-800">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-white font-semibold">Eskalasi vs Selesai</h3>
              <p className="text-gray-400 text-sm">Trend harian</p>
            </div>
          </div>
          {chartData.length > 0 && chartData.some((d) => d.escalated > 0 || d.resolved > 0) ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
                <XAxis dataKey="label" stroke="#6b7280" tick={{ fill: '#6b7280', fontSize: 12 }} />
                <YAxis stroke="#6b7280" tick={{ fill: '#6b7280', fontSize: 12 }} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1a1d27', border: '1px solid #2a2d3a', borderRadius: '8px', color: '#fff' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Legend />
                <Bar dataKey="escalated" fill="#f59e0b" name="Eskalasi" radius={[4, 4, 0, 0]} />
                <Bar dataKey="resolved" fill="#00d563" name="Selesai" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState message="Tiada data eskalasi/selesai untuk tempoh ini" />
          )}
        </div>

        {/* Agent Performance Table */}
        <div className="bg-[#1a1d27] rounded-xl p-6 mb-4 border border-gray-800">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-white font-semibold">Prestasi Staf</h3>
              <p className="text-gray-400 text-sm">Prestasi agent dan perbualan dihandle</p>
            </div>
          </div>
          {agents.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-400 text-xs uppercase tracking-wider border-b border-gray-700">
                    <th className="text-left py-3 px-2 font-medium">Nama</th>
                    <th className="text-right py-3 px-2 font-medium">Perbualan</th>
                    <th className="text-right py-3 px-2 font-medium">Masa Respons</th>
                    <th className="text-right py-3 px-2 font-medium">Eskalasi</th>
                    <th className="text-right py-3 px-2 font-medium">Selesai</th>
                    <th className="text-right py-3 px-2 font-medium">Mesej</th>
                  </tr>
                </thead>
                <tbody>
                  {agents.map((a) => (
                    <tr key={a.id} className="border-b border-gray-800 hover:bg-[#0f1117]/50 transition-colors">
                      <td className="py-3 px-2 text-white font-medium">{a.name}</td>
                      <td className="py-3 px-2 text-right text-gray-300">{a.conversations_handled}</td>
                      <td className="py-3 px-2 text-right text-gray-300">{formatDuration(a.avg_response_time_sec)}</td>
                      <td className="py-3 px-2 text-right text-amber-400">{a.escalated_count}</td>
                      <td className="py-3 px-2 text-right text-emerald-400">{a.resolved_count}</td>
                      <td className="py-3 px-2 text-right text-gray-300">{a.messages_sent}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState message="Tiada data prestasi staf untuk tempoh ini" />
          )}
        </div>

        {/* AI Reply Rate */}
        <div className="bg-[#1a1d27] rounded-xl p-6 border border-gray-800">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-white font-semibold">AI Reply Rate</h3>
              <p className="text-gray-400 text-sm">Peratus mesej dihandle AI tanpa fallback</p>
            </div>
            <div className="text-right">
              <p className="text-3xl font-bold text-white">{(overview.ai_reply_rate || 0).toFixed(1)}%</p>
              <p className={`text-xs mt-1 ${(overview.ai_reply_rate || 0) >= 80 ? 'text-emerald-400' : 'text-amber-400'}`}>
                {(overview.ai_reply_rate || 0) >= 80 ? '✅ AI handle baik' : '⚠️ Ramai perlu staff'}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

