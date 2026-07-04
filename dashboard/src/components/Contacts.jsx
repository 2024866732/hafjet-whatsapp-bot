/**
 * Contacts — Pelanggan page with search, filter, detail panel, CSV import/export
 */
import { useState, useEffect, useRef } from 'react';
import { fetchContacts, updateContact, exportContacts, importContacts, fetchMessages } from '../api/api';
import { formatPhone, formatDate, timeAgo } from '../utils/format';

function Toast({ message, type, onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, 3000);
    return () => clearTimeout(t);
  }, [onClose]);
  const bg = type === 'success' ? 'bg-emerald-600' : type === 'error' ? 'bg-red-600' : 'bg-blue-600';
  return <div className={`fixed top-4 right-4 ${bg} text-white px-4 py-2 rounded-lg shadow-lg text-sm z-50 animate-fade-in`}>{message}</div>;
}

const FILTER_TABS = [
  { id: '', label: 'Semua' },
  { id: 'Baru', label: 'Baru' },
  { id: 'Ulangan', label: 'Ulangan' },
  { id: 'VIP', label: 'VIP' },
  { id: 'Escalated', label: 'Escalated' },
];

function TagBadge({ tag, onRemove, small }) {
  const colors = {
    Baru: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    Ulangan: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
    VIP: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    Escalated: 'bg-red-500/20 text-red-400 border-red-500/30',
  };
  return (
    <span className={`inline-flex items-center gap-1 ${colors[tag] || 'bg-gray-500/20 text-gray-400'} border rounded font-bold ${small ? 'text-[9px] px-1 py-0' : 'text-[10px] px-1.5 py-0.5'}`}>
      {tag}
      {onRemove && <button onClick={() => onRemove(tag)} className="hover:text-white ml-0.5">&times;</button>}
    </span>
  );
}

export default function Contacts() {
  const [contacts, setContacts] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [activeFilter, setActiveFilter] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);
  const [selectedPhone, setSelectedPhone] = useState(null);
  const [detailMessages, setDetailMessages] = useState([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editName, setEditName] = useState('');
  const [editNote, setEditNote] = useState('');
  const [editTags, setEditTags] = useState('');
  const [saving, setSaving] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importCsv, setImportCsv] = useState('');
  const [importing, setImporting] = useState(false);
  const [showCsvUpload, setShowCsvUpload] = useState(false);
  const fileInputRef = useRef(null);

  const showToast = (msg, type = 'success') => setToast({ message: msg, type });

  const loadContacts = async (p = page) => {
    setLoading(true);
    try {
      const data = await fetchContacts({ tag: activeFilter, search: search.trim(), page: p, limit: 20 });
      setContacts(data.contacts || []);
      setTotal(data.total || 0);
      setTotalPages(data.total_pages || 1);
      setPage(data.page || 1);
    } catch (e) {
      showToast('❌ Gagal muat contacts', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadContacts(1); }, [activeFilter]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => loadContacts(1), 400);
    return () => clearTimeout(timer);
  }, [search]);

  const loadDetail = async (phone) => {
    setSelectedPhone(phone);
    setDetailLoading(true);
    try {
      const msgs = await fetchMessages(phone);
      setDetailMessages(msgs || []);
      const contact = contacts.find((c) => c.phone === phone);
      if (contact) {
        setEditName(contact.name || '');
        setEditNote(contact.note || '');
        setEditTags((contact.manual_tags || []).join(', '));
      }
    } catch (e) {
      showToast('❌ Gagal muat detail', 'error');
    } finally {
      setDetailLoading(false);
    }
  };

  const handleSaveDetail = async () => {
    setSaving(true);
    try {
      const tagsStr = editTags.split(',').map((t) => t.trim()).filter(Boolean).join(',');
      await updateContact(selectedPhone, { name: editName, tags: tagsStr, note: editNote });
      showToast('✅ Contact diupdate');
      loadContacts(page);
      // Refresh detail
      if (selectedPhone) loadDetail(selectedPhone);
    } catch (e) {
      showToast('❌ Gagal update', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleExport = async () => {
    try {
      const blob = await exportContacts();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'hafjet-contacts.csv';
      a.click();
      URL.revokeObjectURL(url);
      showToast('✅ CSV diexport');
    } catch (e) {
      showToast('❌ Gagal export', 'error');
    }
  };

  const handleFileUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setImportCsv(ev.target.result);
      setShowCsvUpload(false);
      setShowImport(true);
    };
    reader.readAsText(file);
  };

  const handleImport = async () => {
    setImporting(true);
    try {
      const result = await importContacts(importCsv);
      showToast(`✅ Import: ${result.imported} baru, ${result.updated} update, ${result.errors} ralat`);
      setShowImport(false);
      setImportCsv('');
      loadContacts(1);
    } catch (e) {
      showToast('❌ Gagal import', 'error');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="flex-1 flex overflow-hidden bg-[#0f1117]">
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

      {/* Left: Contact List */}
      <div className="w-[340px] min-w-[340px] border-r border-gray-800 flex flex-col">
        {/* Search + Filters */}
        <div className="p-3 border-b border-gray-800">
          <div className="relative mb-2">
            <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Cari nama atau nombor..."
              className="w-full bg-gray-800 text-gray-200 text-xs rounded-lg pl-8 pr-3 py-2 border border-gray-700 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div className="flex gap-1 flex-wrap">
            {FILTER_TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveFilter(tab.id)}
                className={`text-[10px] font-medium px-2 py-1 rounded-lg transition-colors ${
                  activeFilter === tab.id
                    ? 'bg-emerald-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:text-gray-200'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Contact Cards */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-5 h-5 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : contacts.length === 0 ? (
            <div className="text-center py-12 px-4">
              <svg className="w-10 h-10 mx-auto text-gray-600 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <p className="text-gray-500 text-xs">Tiada contact</p>
            </div>
          ) : (
            contacts.map((c) => {
              const tags = c.all_tags || [];
              return (
                <button
                  key={c.phone}
                  onClick={() => loadDetail(c.phone)}
                  className={`w-full text-left px-3 py-2.5 border-b border-gray-800 hover:bg-gray-800/50 transition-colors ${
                    selectedPhone === c.phone ? 'bg-gray-800 border-l-2 border-l-emerald-500' : ''
                  }`}
                >
                  <div className="flex items-start gap-2.5">
                    <div className="w-8 h-8 bg-gray-700 rounded-full flex items-center justify-center flex-shrink-0">
                      <span className="text-gray-300 text-xs font-medium">
                        {(c.name || c.phone)?.[0]?.toUpperCase() || '?'}
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <span className="text-gray-200 text-xs font-medium truncate">
                          {c.name || formatPhone(c.phone)}
                        </span>
                        <span className="text-gray-500 text-[9px] flex-shrink-0 ml-1">
                          {c.last_contact ? timeAgo(c.last_contact) : '-'}
                        </span>
                      </div>
                      <p className="text-gray-500 text-[10px] mt-0.5">{formatPhone(c.phone)}</p>
                      {tags.length > 0 && (
                        <div className="flex gap-1 mt-1 flex-wrap">
                          {tags.slice(0, 3).map((t) => <TagBadge key={t} tag={t} small />)}
                          {tags.length > 3 && <span className="text-[9px] text-gray-500">+{tags.length - 3}</span>}
                        </div>
                      )}
                      <p className="text-gray-500 text-[9px] mt-1">{c.total_messages || 0} mesej</p>
                    </div>
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="p-2 border-t border-gray-800 flex items-center justify-center gap-2">
            <button
              onClick={() => loadContacts(page - 1)}
              disabled={page <= 1}
              className="text-[10px] text-gray-400 hover:text-white disabled:opacity-30 px-2 py-1 rounded bg-gray-800"
            >
              ← Prev
            </button>
            <span className="text-[10px] text-gray-500">{page} / {totalPages}</span>
            <button
              onClick={() => loadContacts(page + 1)}
              disabled={page >= totalPages}
              className="text-[10px] text-gray-400 hover:text-white disabled:opacity-30 px-2 py-1 rounded bg-gray-800"
            >
              Next →
            </button>
          </div>
        )}

        {/* Bottom Actions */}
        <div className="p-2 border-t border-gray-800 flex gap-2">
          <button onClick={handleExport} className="flex-1 bg-gray-800 hover:bg-gray-700 text-gray-300 text-[10px] py-1.5 rounded transition-colors">
            Export CSV
          </button>
          <button
            onClick={() => {
              setShowCsvUpload(true);
              setTimeout(() => fileInputRef.current?.click(), 50);
            }}
            className="flex-1 bg-gray-800 hover:bg-gray-700 text-gray-300 text-[10px] py-1.5 rounded transition-colors"
          >
            Import CSV
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            className="hidden"
            onChange={handleFileUpload}
          />
        </div>
      </div>

      {/* Right: Contact Detail */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selectedPhone ? (
          <>
            {/* Detail Header */}
            <div className="p-4 border-b border-gray-800">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-gray-700 rounded-full flex items-center justify-center">
                  <span className="text-gray-200 text-base font-medium">
                    {(editName || selectedPhone)?.[0]?.toUpperCase() || '?'}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder="Nama contact..."
                    className="bg-transparent text-white text-sm font-medium w-full focus:outline-none border-b border-transparent focus:border-emerald-500 pb-0.5"
                  />
                  <p className="text-gray-500 text-xs mt-0.5">{formatPhone(selectedPhone)}</p>
                </div>
                <button
                  onClick={handleSaveDetail}
                  disabled={saving}
                  className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-xs px-3 py-1.5 rounded-lg transition-colors"
                >
                  {saving ? 'Menyimpan...' : 'Simpan'}
                </button>
              </div>

              {/* Tags */}
              <div className="mt-3">
                <label className="text-gray-500 text-[10px] font-medium block mb-1">Tags (pisah guna koma)</label>
                <input
                  value={editTags}
                  onChange={(e) => setEditTags(e.target.value)}
                  placeholder="VIP, Ulangan, Baru..."
                  className="w-full bg-gray-800 text-gray-200 text-xs rounded p-1.5 border border-gray-700 focus:border-emerald-500 focus:outline-none"
                />
              </div>
              <div className="mt-2">
                <label className="text-gray-500 text-[10px] font-medium block mb-1">Note</label>
                <textarea
                  value={editNote}
                  onChange={(e) => setEditNote(e.target.value)}
                  placeholder="Nota tentang contact ini..."
                  rows={2}
                  className="w-full bg-gray-800 text-gray-200 text-xs rounded p-1.5 border border-gray-700 focus:border-emerald-500 focus:outline-none resize-none"
                />
              </div>
            </div>

            {/* Conversation History */}
            <div className="flex-1 overflow-y-auto p-4">
              <h3 className="text-gray-400 text-[10px] font-semibold uppercase tracking-wider mb-3">Conversation History</h3>
              {detailLoading ? (
                <div className="flex items-center justify-center py-8">
                  <div className="w-5 h-5 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
                </div>
              ) : detailMessages.length === 0 ? (
                <p className="text-gray-500 text-xs text-center py-8">Tiada mesej</p>
              ) : (
                <div className="space-y-2">
                  {detailMessages.map((msg) => (
                    <div
                      key={msg.id}
                      className={`flex gap-2 ${msg.direction === 'inbound' ? '' : 'flex-row-reverse'}`}
                    >
                      <div className={`max-w-[70%] rounded-lg px-2.5 py-1.5 ${
                        msg.direction === 'inbound'
                          ? 'bg-gray-800 text-gray-200'
                          : 'bg-emerald-600/20 text-emerald-300'
                      }`}>
                        <p className="text-xs">{msg.message || msg.content}</p>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[8px] text-gray-500">{msg.timestamp ? formatDate(msg.timestamp) : ''}</span>
                          {msg.routing_path && (
                            <span className="text-[8px] text-gray-500 bg-gray-700 px-1 rounded">{msg.routing_path}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex items-center justify-center h-full">
            <div className="text-center px-6">
              <svg className="w-12 h-12 mx-auto text-gray-600 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <p className="text-gray-500 text-sm">Pilih contact untuk lihat detail</p>
            </div>
          </div>
        )}
      </div>

      {/* Import Dialog */}
      {showImport && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-xl p-5 max-w-lg w-full mx-4 border border-gray-700 shadow-2xl">
            <h3 className="text-white font-semibold text-sm mb-3">Import CSV</h3>
            <textarea
              value={importCsv}
              onChange={(e) => setImportCsv(e.target.value)}
              className="w-full bg-gray-700 text-gray-200 text-xs p-2 rounded border border-gray-600 h-32 focus:outline-none focus:border-emerald-500"
              placeholder="Paste CSV content..."
            />
            <div className="flex gap-3 mt-3">
              <button
                onClick={() => { setShowImport(false); setImportCsv(''); }}
                className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs py-2 rounded transition-colors"
              >
                Batal
              </button>
              <button
                onClick={handleImport}
                disabled={importing || !importCsv.trim()}
                className="flex-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-xs py-2 rounded transition-colors"
              >
                {importing ? 'Mengimport...' : 'Import'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
