"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Loader2, AlertCircle, CalendarClock, Calendar as CalendarIcon, ArrowRight, ArrowLeft, Check, FileText, Camera, Scissors, CheckSquare, Send, ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { API_URL } from '@/lib/config';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { PageHeader } from '@/components/common/PageHeader';
import { toast } from 'sonner';

export default function PendingWorkPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  
  const [entries, setEntries] = useState<any[]>([]);
  const [clients, setClients] = useState<any[]>([]);
  const [clientProjects, setClientProjects] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [activeClientId, setActiveClientId] = useState<string | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  const [inlineInputs, setInlineInputs] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const storedUser = localStorage.getItem('user');
      const user = storedUser ? JSON.parse(storedUser) : null;
      
      const [entriesRes, clientsRes, pRes] = await Promise.all([
        fetch(`${API_URL}/content-calendar/all`),
        fetch(`${API_URL}/clients`),
        fetch(`${API_URL}/projects${user ? `?userId=${user.id}&role=${user.role}` : ''}`)
      ]);
      
      if (entriesRes.ok && clientsRes.ok) {
        const fetchedEntries = await entriesRes.json();
        const fetchedClients = await clientsRes.json();
        setEntries(fetchedEntries);
        setClients(fetchedClients);
      }
      
      if (pRes.ok) {
        const projects = await pRes.json();
        const projectMap: Record<string, any> = {};
        projects.forEach((p: any) => {
          if (p.department === 'Creative') {
            projectMap[p.id] = p;
          }
        });
        setClientProjects(projectMap);
      }
    } catch (error) {
      console.error('Failed to fetch dashboard data', error);
    } finally {
      setLoading(false);
    }
  };

  const handleInlineComplete = async (entryId: string, updates: Record<string, any>) => {
    setSavingId(entryId);
    try {
      const storedUser = localStorage.getItem('user');
      const user = storedUser ? JSON.parse(storedUser) : null;
      const userName = user?.name || (user?.firstName ? `${user.firstName} ${user.lastName || ''}`.trim() : null) || "Unknown User";

      const res = await fetch(`${API_URL}/content-calendar/${entryId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...updates, updatedBy: userName }),
      });

      if (res.ok) {
        const updatedEntry = await res.json();
        setEntries(prev => prev.map(e => e.id === entryId ? updatedEntry : e));
        toast.success('Task marked as complete!');
        setInlineInputs(prev => {
          const next = { ...prev };
          delete next[entryId];
          return next;
        });
      } else {
        toast.error('Failed to update task');
      }
    } catch (err) {
      console.error(err);
      toast.error('Network error');
    } finally {
      setSavingId(null);
    }
  };

  const pendingData = useMemo(() => {
    const clientsMap: Record<string, { id: string, name: string, count: number, tasks: any[], projectId?: string, clientId: string }> = {};

    entries.forEach(entry => {
      const client = clients.find(c => c.id === entry.clientId);
      const clientName = client ? (client.companyName || client.clientName || 'Unknown Client') : 'Unknown Client';
      const projectId = entry.projectId;
      const project = projectId ? clientProjects[projectId] : null;
      const displayName = project ? `${project.title} (${clientName})` : clientName;
      const key = projectId || entry.clientId;

      if (!clientsMap[key]) {
        clientsMap[key] = { id: key, name: displayName, count: 0, tasks: [], projectId, clientId: entry.clientId };
      }

      const enrich = (stage: string, deadline: string, type: string) => ({
        ...entry,
        clientName,
        stage,
        deadline,
        type
      });

      const isPost = entry.postReel === 'Post';
      const isStory = entry.postReel === 'Story';
      if (!isPost && !isStory && entry.scriptDate && entry.scriptDate !== '-' && !entry.scriptLink) clientsMap[key].tasks.push(enrich('Script', entry.scriptDate, 'scripts'));
      if (!isPost && !isStory && entry.shootDate && entry.shootDate !== '-' && !entry.shootLink && entry.shootLink !== '-') clientsMap[key].tasks.push(enrich('Shoot', entry.shootDate, 'shoots'));
      
      const captionDate = entry.captionDate || entry.editingStart;
      if (!isStory && captionDate && captionDate !== '-' && !entry.caption && entry.caption !== '-') clientsMap[key].tasks.push(enrich('Caption', captionDate, 'captions'));
      
      const thumbnailDate = entry.thumbnailDate || entry.editingStart;
      if (!isPost && !isStory && thumbnailDate && thumbnailDate !== '-' && !entry.thumbnailLink && entry.thumbnailLink !== '-') clientsMap[key].tasks.push(enrich('Thumbnail', thumbnailDate, 'thumbnails'));
      
      const isEditingPending = entry.editingStart && entry.editingStart !== '-' && (isPost ? !entry.finalPostLink : !entry.finalReelLink);
      if (isEditingPending) clientsMap[key].tasks.push(enrich('Editing', entry.editingStart, 'edits'));
      if (entry.approval && entry.approval !== '-' && entry.isApproved !== 'Yes') clientsMap[key].tasks.push(enrich('Approval', entry.approval, 'approvals'));
      if (!isStory && entry.postingDate && entry.postingDate !== '-' && !entry.postingLinkOfIg && entry.postingLinkOfIg !== '-') clientsMap[key].tasks.push(enrich('Posting', entry.postingDate, 'posts'));
    });

    return Object.values(clientsMap)
      .filter(c => c.tasks.length > 0)
      .map(c => {
        c.count = c.tasks.length;
        c.tasks.sort((a, b) => new Date(a.deadline).getTime() - new Date(b.deadline).getTime());
        return c;
      })
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [entries, clients, clientProjects]);

  useEffect(() => {
    if (pendingData.length > 0 && !activeClientId) {
      const clientParam = searchParams.get('client');
      const projectParam = searchParams.get('projectId');
      const targetId = projectParam || clientParam;
      
      if (targetId && pendingData.find(c => c.id === targetId)) {
        setActiveClientId(targetId);
      } else if (clientParam && pendingData.find(c => c.clientId === clientParam)) {
        setActiveClientId(pendingData.find(c => c.clientId === clientParam)!.id);
      } else {
        setActiveClientId(pendingData[0].id);
      }
    }
  }, [pendingData, searchParams, activeClientId]);

  const currentClient = pendingData.find(c => c.id === activeClientId);
  const currentList = currentClient?.tasks || [];

  const groupedTasks = {
    scripts: currentList.filter(t => t.type === 'scripts'),
    shoots: currentList.filter(t => t.type === 'shoots'),
    edits: currentList.filter(t => t.type === 'edits'),
    approvals: currentList.filter(t => t.type === 'approvals'),
    posts: currentList.filter(t => t.type === 'posts'),
  };

  const toggleGroup = (type: string) => {
    setCollapsedGroups(prev => ({ ...prev, [type]: !prev[type] }));
  };

  const renderTaskGroup = (title: string, icon: React.ReactNode, tasks: any[], type: string) => {
    if (tasks.length === 0) return null;
    const isCollapsed = collapsedGroups[type];

    return (
      <div className="mb-6 bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
        <button 
          onClick={() => toggleGroup(type)}
          className="w-full text-left text-sm font-bold text-slate-600 uppercase tracking-wider p-4 flex items-center justify-between bg-slate-50/50 hover:bg-slate-100 transition-colors"
        >
          <div className="flex items-center gap-2">
            {icon} {title} ({tasks.length})
          </div>
          {isCollapsed ? <ChevronRight className="w-5 h-5 text-slate-400" /> : <ChevronDown className="w-5 h-5 text-slate-400" />}
        </button>
        
        {!isCollapsed && (
          <div className="p-4 grid gap-3 border-t border-slate-200">
            {tasks.map((item, idx) => {
              const isOverdue = new Date(item.deadline) < new Date(new Date().setHours(0,0,0,0));
              return (
                <div key={idx} className="bg-white border border-slate-200 rounded-lg shadow-sm hover:border-brand-teal/30 hover:shadow-md transition-all p-3 flex flex-col md:flex-row md:items-center gap-4">
                  
                  {/* Left Info Section */}
                  <div className="flex-1 min-w-0 flex items-center gap-3">
                    <Badge variant={isOverdue ? "destructive" : "secondary"} className="text-[10px] px-2 py-0.5 whitespace-nowrap min-w-[75px] justify-center">
                      {item.deadline}
                    </Badge>
                    
                    <div className="flex-1 min-w-0 flex items-center gap-2">
                      <span className="text-sm text-slate-800 font-semibold truncate">
                        {item.concept || item.topic || (item.postReel ? `${item.postReel} Content` : `Task for ${item.postingDate || item.monthYear || 'Unknown Date'}`)}
                      </span>
                      <Badge variant="outline" className="text-[9px] px-1 h-4 shrink-0 font-mono text-slate-400 border-slate-200 ml-1">
                        {item.monthYear}
                      </Badge>
                    </div>
                  </div>
                  
                  {/* Right Action Section */}
                  <div className="flex items-center gap-3 shrink-0">
                    {type === 'approvals' && (
                      <Button 
                        onClick={() => handleInlineComplete(item.id, { isApproved: 'Yes' })}
                        disabled={savingId === item.id}
                        size="sm"
                        className="bg-emerald-600 hover:bg-emerald-700 text-white h-9 text-xs min-w-[140px]"
                      >
                        {savingId === item.id ? <Loader2 className="w-3 h-3 mr-1.5 animate-spin" /> : <Check className="w-3 h-3 mr-1.5" />}
                        Approve Content
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="outline" size="icon" onClick={() => router.back()} className="shrink-0 bg-white" title="Back">
          <ArrowLeft className="w-4 h-4" />
        </Button>
        <div className="flex-1 flex flex-col md:flex-row md:items-center justify-between gap-4">
          <PageHeader
            title="Global Pending Work"
            description="Track all pending content calendar items sorted by project."
          />
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden min-h-[calc(100vh-250px)] flex flex-col">
        {loading ? (
          <div className="flex-1 flex items-center justify-center p-20">
            <Loader2 className="w-8 h-8 animate-spin text-brand-teal" />
          </div>
        ) : pendingData.length === 0 ? (
          <div className="text-center py-20">
            <AlertCircle className="w-12 h-12 text-slate-300 mx-auto mb-4" />
            <h3 className="text-xl font-medium text-slate-700">No pending work anywhere!</h3>
            <p className="text-slate-500 mt-2">All projects are completely up to date.</p>
          </div>
        ) : (
          <div className="flex-1 flex flex-col md:flex-row overflow-hidden">
            <div className="w-full md:w-72 border-b md:border-b-0 md:border-r bg-slate-50/50 flex flex-col p-4 gap-2 overflow-y-auto">
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2 px-1">Projects with Pending Work</h3>
              {pendingData.map(client => (
                <button
                  key={client.id}
                  onClick={() => setActiveClientId(client.id)}
                  className={`flex items-center justify-between px-3 py-3 rounded-lg text-sm font-medium transition-colors ${
                    activeClientId === client.id 
                      ? 'bg-brand-teal/10 text-brand-teal shadow-sm border border-brand-teal/20' 
                      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 border border-transparent'
                  }`}
                >
                  <span className="truncate pr-2 text-left">{client.name}</span>
                  <Badge variant="secondary" className={`ml-auto shrink-0 border-none ${activeClientId === client.id ? 'bg-brand-teal/20 text-brand-teal hover:bg-brand-teal/20' : 'bg-slate-200 text-slate-500 hover:bg-slate-200'}`}>
                    {client.count}
                  </Badge>
                </button>
              ))}
            </div>
            
            <div className="flex-1 overflow-y-auto p-6 bg-slate-50">
              <div className="mb-8 flex items-center justify-between bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
                <div>
                  <h2 className="text-xl font-bold text-slate-800">{currentClient?.name}</h2>
                  <p className="text-sm text-slate-500 mt-0.5">{currentList.length} total pending tasks</p>
                </div>
                <Button 
                  onClick={() => router.push(`/work-management/smm/${currentClient?.clientId}${currentClient?.projectId ? `?projectId=${currentClient.projectId}` : ''}`)}
                  variant="outline"
                  size="sm"
                  className="text-brand-teal border-brand-teal/20 hover:bg-brand-teal/5 h-9 gap-2"
                >
                  <CalendarIcon className="w-4 h-4" /> Open Full Calendar
                </Button>
              </div>

              {currentList.length === 0 ? (
                <div className="text-center py-12 bg-white rounded-xl border border-dashed">
                  <AlertCircle className="w-8 h-8 text-slate-300 mx-auto mb-3" />
                  <h3 className="text-lg font-medium text-slate-700">No pending work here!</h3>
                  <p className="text-sm text-slate-500 mt-1">Everything is up to date in this stage.</p>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {renderTaskGroup("Pending Scripts", <FileText className="w-4 h-4" />, groupedTasks.scripts, 'scripts')}
                  {renderTaskGroup("Pending Shoots", <Camera className="w-4 h-4" />, groupedTasks.shoots, 'shoots')}
                  {renderTaskGroup("Pending Edits / Graphics", <Scissors className="w-4 h-4" />, groupedTasks.edits, 'edits')}
                  {renderTaskGroup("Pending Approvals", <CheckSquare className="w-4 h-4" />, groupedTasks.approvals, 'approvals')}
                  {renderTaskGroup("Pending Posts", <Send className="w-4 h-4" />, groupedTasks.posts, 'posts')}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
