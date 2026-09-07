"use client";

import React, { useState, useEffect, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/common/PageHeader";
import { 
  Users,
  User, 
  Plus, 
  Pencil, 
  Trash2, 
  Mail, 
  Phone, 
  Search, 
  Loader2, 
  LayoutGrid,
  History,
  ClipboardList,
  Filter,
  AlertCircle,
  CheckCircle2,
  CalendarClock,
  Banknote,
  CreditCard,
  Star,
  UserPlus,
  SlidersHorizontal,
  Video,
  Image as ImageIcon,
  PenTool,
  ChevronDown,
  ChevronsUpDown,
  Check,
  MoreHorizontal,
  ChevronLeft
} from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { ClientForm, ClientFormData } from "@/components/hrms/ClientForm";
import { API_URL } from "@/lib/config";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { useUser } from "@/hooks/useUser";
import { toast } from "sonner";
import { useConfirm } from "@/context/ConfirmContext";
import { ActivityLogDialog } from "@/components/common/ActivityLogDialog";
import { WhatsAppSmmDialog } from "@/components/hrms/WhatsAppSmmDialog";
import { WhatsAppIcon } from "@/components/hrms/WhatsAppIcon";
import { SmmMeetingDialog } from "@/components/hrms/SmmMeetingDialog";
import { PendingWorkEmbedded } from "@/components/hrms/PendingWorkEmbedded";
import { OtherWorkDialog } from "@/components/hrms/OtherWorkDialog";
import { FeedbackReviewsEmbedded } from "@/components/hrms/FeedbackReviewsEmbedded";
import { ClientReviewDialog } from "@/components/hrms/ClientReviewDialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator, DropdownMenuLabel } from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { DailyProgressView } from "@/components/hrms/DailyProgressView";

const SearchableEmployeeSelect = ({ value, onChange, placeholder, employees }: { value: string, onChange: (val: string) => void, placeholder: string, employees: any[] }) => {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const selectedEmp = employees.find((e: any) => e.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" role="combobox" aria-expanded={open} className="w-full justify-between font-normal h-10 border-slate-200">
          {selectedEmp ? (selectedEmp.name || `${selectedEmp.firstName} ${selectedEmp.lastName}`) : (value === "none" ? <span className="italic text-slate-500">None</span> : <span className="text-slate-500">{placeholder}</span>)}
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <div className="flex items-center border-b px-3 bg-slate-50/50">
          <Search className="mr-2 h-4 w-4 shrink-0 opacity-50 text-slate-500" />
          <input 
            className="flex h-10 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-slate-400"
            placeholder="Search employee..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="max-h-56 overflow-y-auto p-1">
          <div
             className={`relative flex cursor-pointer select-none items-center rounded-sm px-2 py-2 text-sm outline-none hover:bg-slate-100 transition-colors ${value === "none" ? "bg-slate-100 font-medium text-slate-900" : "text-slate-600"}`}
             onClick={() => { onChange("none"); setOpen(false); setSearch(""); }}
          >
            <Check className={`mr-2 h-4 w-4 text-brand-teal ${value === "none" ? "opacity-100" : "opacity-0"}`} />
            <span className="italic">None</span>
          </div>
          {employees.filter((e: any) => {
            const term = search.toLowerCase();
            const name = (e.name || `${e.firstName} ${e.lastName}`).toLowerCase();
            const dept = (e.department || "").toLowerCase();
            return name.includes(term) || dept.includes(term);
          }).map((emp: any) => (
             <div 
               key={emp.id}
               className={`relative flex justify-between cursor-pointer select-none items-center rounded-sm px-2 py-2 text-sm outline-none hover:bg-slate-100 transition-colors ${value === emp.id ? "bg-slate-100 font-medium text-slate-900" : "text-slate-700"}`}
               onClick={() => { onChange(emp.id); setOpen(false); setSearch(""); }}
             >
               <div className="flex items-center truncate">
                 <Check className={`mr-2 h-4 w-4 shrink-0 text-brand-teal ${value === emp.id ? "opacity-100" : "opacity-0"}`} />
                 <span className="truncate">{emp.name || `${emp.firstName} ${emp.lastName}`}</span>
               </div>
               {emp.department && <span className="ml-2 shrink-0 text-[10px] bg-white border border-slate-200 text-slate-500 px-1.5 py-0.5 rounded-md truncate max-w-[100px]">{emp.department}</span>}
             </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
};

const noScrollbarStyle = `
  .no-scrollbar::-webkit-scrollbar,
  [data-slot="table-container"]::-webkit-scrollbar {
    display: none !important;
  }
  .no-scrollbar,
  [data-slot="table-container"] {
    -ms-overflow-style: none !important;
    scrollbar-width: none !important;
  }
`;

function EditableCell({ client, field, type = "text", options = [], value, render, align = "left", handleInlineUpdate, inlineEditing, setInlineEditing }: any) {
  const isEditing = inlineEditing?.id === client.id && inlineEditing?.field === field;
  return (
    <td 
      className={`px-4 py-4 cursor-pointer hover:bg-slate-50/80 transition-colors ${align === 'center' ? 'text-center' : ''}`}
      onClick={() => setInlineEditing({ id: client.id, field })}
    >
      {isEditing ? (
        type === 'select' ? (
          <select 
            autoFocus
            className="w-full border rounded px-1 py-0.5 outline-none text-xs"
            defaultValue={client[field]}
            onBlur={(e) => handleInlineUpdate(client.id, field, e.target.value)}
            onChange={(e) => handleInlineUpdate(client.id, field, e.target.value)}
          >
            {options.map((o: any) => <option key={o} value={o}>{o}</option>)}
          </select>
        ) : (
          <input 
            autoFocus
            type={type}
            className={`w-full border rounded px-2 py-1 outline-none text-sm ${align === 'center' ? 'text-center' : ''}`}
            defaultValue={client[field]}
            onBlur={(e) => handleInlineUpdate(client.id, field, e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleInlineUpdate(client.id, field, e.currentTarget.value)}
          />
        )
      ) : render(client[field])}
    </td>
  );
}

export default function CreativeClientsPage() {
  const router = useRouter();
  const { confirm } = useConfirm();
  const { user } = useUser();
  const hasFullSmmAccess = React.useMemo(() => {
    if (!user) return false;
    const r = (user.role || "").toLowerCase();
    const d = (user.designation || "").toLowerCase();
    const fullRoles = ["admin", "manager", "social media manager", "smm", "director", "head", "super admin", "digital marketer", "digital marketing", "sub admin", "sub-admin", "hr", "team leader", "tl"];
    if (
      fullRoles.includes(r) || 
      fullRoles.includes(d) || 
      r.includes("social media") || 
      d.includes("social media") || 
      r.includes("digital marketing") || 
      d.includes("digital marketing") ||
      d.includes("team leader") ||
      d.includes("tl") ||
      d.includes("head") ||
      r.includes("team leader") ||
      r.includes("tl") ||
      r.includes("head")
    ) {
      return true;
    }

    const perms = (user as any).permissions || [];
    const smmPerms = ["projects", "smm", "clients", "digital-marketing", "work-management"];
    return perms.some((p: any) => smmPerms.includes(p.moduleName) && (p.canView || p.canEdit || p.canAdd));
  }, [user]);

  const isRealAdmin = user?.role?.toLowerCase() === "admin";
  const isAdmin = isRealAdmin || hasFullSmmAccess;
  const isEmployeeOrIntern = (user?.role === "Employee" || user?.role === "Intern") && !hasFullSmmAccess;

  const canViewAllClients = React.useMemo(() => {
    if (!user) return false;
    const r = (user.role || "").toLowerCase();
    const d = (user.designation || "").toLowerCase();
    const allAccessRoles = ["admin", "super admin", "manager", "director", "head", "sub admin", "sub-admin", "hr", "team leader", "tl"];
    
    if (
      allAccessRoles.includes(r) || 
      allAccessRoles.includes(d) || 
      d.includes("head") || 
      d.includes("manager") || 
      d.includes("team leader") ||
      d.includes("tl") ||
      r.includes("admin") ||
      r.includes("team leader") ||
      r.includes("tl") ||
      r.includes("head")
    ) {
      return true;
    }
    
    return false;
  }, [user]);

  const [clients, setClients] = useState<any[]>([]);
  const [calendarEntries, setCalendarEntries] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingClient, setEditingClient] = useState<any>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const searchParams = useSearchParams();
  const [masterFilter, setMasterFilter] = useState(searchParams.get("tab") || "all");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (masterFilter === "all") {
      params.delete("tab");
    } else {
      params.set("tab", masterFilter);
    }
    const newSearch = params.toString();
    const newUrl = `${window.location.pathname}${newSearch ? "?" + newSearch : ""}`;
    router.replace(newUrl);
  }, [masterFilter, router]);
  const [inlineEditing, setInlineEditing] = useState<{id: string, field: string} | null>(null);

  // Advanced Filters
  const [creativeFilter, setCreativeFilter] = useState("all");
  const [calendarFilterMonth, setCalendarFilterMonth] = useState(`${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, "0")}`);
  const [calendarFilterStatus, setCalendarFilterStatus] = useState("all");
  const [activeTab, setActiveTab] = useState("projects");
  
  // Creative Team Assignment
  const [creativeEmployees, setCreativeEmployees] = useState<any[]>([]);
  const [assignTeamClient, setAssignTeamClient] = useState<any>(null);
  const [assignTeamProject, setAssignTeamProject] = useState<any>(null);
  const [assignTeamOpen, setAssignTeamOpen] = useState(false);
  const [scriptwriterId, setScriptwriterId] = useState("");
  const [reelEditorId, setReelEditorId] = useState("");
  const [postDesignerId, setPostDesignerId] = useState("");
  const [shooterId, setShooterId] = useState("");
  const [approverId, setApproverId] = useState("");
  const [posterId, setPosterId] = useState("");
  const [captionWriterId, setCaptionWriterId] = useState("");
  const [thumbnailDesignerId, setThumbnailDesignerId] = useState("");
  const [whatsappGroupCreatorId, setWhatsappGroupCreatorId] = useState("");
  const [greetingsMsgSenderId, setGreetingsMsgSenderId] = useState("");
  const [meetingsAssigneeId, setMeetingsAssigneeId] = useState("");
  const [contentCalendarCreatorId, setContentCalendarCreatorId] = useState("");
  const [followupAssigneeId, setFollowupAssigneeId] = useState("");

  const [logsOpen, setLogsOpen] = useState(false);
  const [clientLogs, setClientLogs] = useState<any[]>([]);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [activeClient, setActiveClient] = useState<any>(null);
  const [waDialogOpen, setWaDialogOpen] = useState(false);
  const [waClient, setWaClient] = useState<any>(null);

  const [followupConfigOpen, setFollowupConfigOpen] = useState(false);
  const [greetingsLogsOpen, setGreetingsLogsOpen] = useState(false);
  const [greetingsLogsClient, setGreetingsLogsClient] = useState<any>(null);

  const [reviewDialogOpen, setReviewDialogOpen] = useState(false);
  const [reviewClient, setReviewClient] = useState<any>(null);

  const [followupConfigClient, setFollowupConfigClient] = useState<any>(null);
  const [followupConfigProject, setFollowupConfigProject] = useState<any>(null);
  const [followupTypeInput, setFollowupTypeInput] = useState("Interval");
  const [followupIntervalInput, setFollowupIntervalInput] = useState("");
  const [followupDaysOfWeekInput, setFollowupDaysOfWeekInput] = useState<number[]>([]);
  const [followupDatesOfMonthInput, setFollowupDatesOfMonthInput] = useState<number[]>([]);
  const [followupLastDateInput, setFollowupLastDateInput] = useState("");

  const [followupRemarkOpen, setFollowupRemarkOpen] = useState(false);
  const [followupRemarkClient, setFollowupRemarkClient] = useState<any>(null);
  const [followupRemarkProject, setFollowupRemarkProject] = useState<any>(null);
  const [followupRemarkText, setFollowupRemarkText] = useState("");

  const [followupHistoryLogs, setFollowupHistoryLogs] = useState<any[]>([]);
  const [isLoadingFollowupHistory, setIsLoadingFollowupHistory] = useState(false);
  
  const [feedbackConfigOpen, setFeedbackConfigOpen] = useState(false);
  const [feedbackConfigProject, setFeedbackConfigProject] = useState<any>(null);
  const [feedbackTypeInput, setFeedbackTypeInput] = useState("Interval");
  const [feedbackIntervalInput, setFeedbackIntervalInput] = useState("");
  const [feedbackDaysOfWeekInput, setFeedbackDaysOfWeekInput] = useState<number[]>([]);
  const [feedbackDatesOfMonthInput, setFeedbackDatesOfMonthInput] = useState<number[]>([]);
  const [feedbackLastDateInput, setFeedbackLastDateInput] = useState("");
  const [feedbackNextDateInput, setFeedbackNextDateInput] = useState("");
  
  const [newRemarkText, setNewRemarkText] = useState("");
  const [isAddingRemark, setIsAddingRemark] = useState(false);

  const [editingRemarkId, setEditingRemarkId] = useState<string | null>(null);
  const [editingRemarkText, setEditingRemarkText] = useState("");

  const fetchFollowupHistory = async (client: any, project: any = null) => {
    setIsLoadingFollowupHistory(true);
    try {
      const proj = project || followupConfigProject || (clientProjects[client.id] && clientProjects[client.id][0]);
      if (!proj) {
        setFollowupHistoryLogs([]);
        setIsLoadingFollowupHistory(false);
        return;
      }
      const param = `projectId=${proj.id}`;
      const res = await fetch(`${API_URL}/task-logs?${param}`);
      if (res.ok) {
        const data = await res.json();
        setFollowupHistoryLogs(data.filter((l: any) => l.action === "Follow-up Completed"));
      }
    } catch (err) {
      console.error("Error fetching logs:", err);
    } finally {
      setIsLoadingFollowupHistory(false);
    }
  };

  const handleAddRemark = async () => {
    if (!followupConfigClient || !newRemarkText.trim()) return;
    setIsAddingRemark(true);
    try {
      const proj = followupConfigProject;
      const res = await fetch(`${API_URL}/task-logs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "Follow-up Completed",
          details: `Remark: ${newRemarkText}`,
          clientId: followupConfigClient.id,
          projectId: proj?.id,
          performedBy: user?.id,
          userName: user?.name || `${user?.firstName} ${user?.lastName}`,
        })
      });
      if (res.ok) {
        toast.success("Remark added");
        setNewRemarkText("");
        fetchFollowupHistory(followupConfigClient, proj);
        
        const today = new Date().toISOString().split('T')[0];
        const projectId = proj?.id;
        
        if (projectId) {
          await fetch(`${API_URL}/projects/${projectId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ 
              lastFollowupDate: today,
              performedBy: user?.id,
              userName: user?.name || `${user?.firstName} ${user?.lastName}`,
            }),
          });
          fetchClients();
        }
      } else {
        toast.error("Failed to add remark");
      }
    } catch (err) {
      console.error("Error adding remark:", err);
      toast.error("An error occurred");
    } finally {
      setIsAddingRemark(false);
    }
  };

  const handleUpdateRemark = async (logId: string) => {
    if (!editingRemarkText.trim() || !followupConfigClient) return;
    try {
      const res = await fetch(`${API_URL}/task-logs/${logId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ details: `Remark: ${editingRemarkText}` })
      });
      if (res.ok) {
        toast.success("Remark updated");
        setEditingRemarkId(null);
        fetchFollowupHistory(followupConfigClient, followupConfigProject);
      } else {
        toast.error("Failed to update remark");
      }
    } catch (err) {
      console.error("Error updating remark:", err);
    }
  };

  const handleDeleteRemark = async (logId: string) => {
    if (!followupConfigClient || !window.confirm("Delete this remark?")) return;
    try {
      const res = await fetch(`${API_URL}/task-logs/${logId}`, { method: "DELETE" });
      if (res.ok) {
        toast.success("Remark deleted");
        fetchFollowupHistory(followupConfigClient, followupConfigProject);
      } else {
        toast.error("Failed to delete remark");
      }
    } catch (err) {
      console.error("Error deleting remark:", err);
    }
  };

  const fetchLogs = async (client: any, project: any = null) => {
    setIsLoadingLogs(true);
    setLogsOpen(true);
    setActiveClient(client);
    try {
      const proj = project || (clientProjects[client.id] && clientProjects[client.id][0]);
      if (!proj) {
        setClientLogs([]);
        setIsLoadingLogs(false);
        return;
      }
      const param = `projectId=${proj.id}`;
      const res = await fetch(`${API_URL}/task-logs?${param}`);
      if (res.ok) {
        setClientLogs(await res.json());
      }
    } catch (err) {
      console.error("Error fetching client logs:", err);
    } finally {
      setIsLoadingLogs(false);
    }
  };

  const handleInlineUpdate = async (clientId: string, field: string, value: any) => {
    try {
      const res = await fetch(`${API_URL}/clients/${clientId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          [field]: value,
          performedBy: user?.id,
          userName: user?.name || `${user?.firstName} ${user?.lastName}`,
        }),
      });
      if (res.ok) {
        setClients(prev => prev.map(c => c.id === clientId ? { ...c, [field]: value } : c));
      } else {
        toast.error("Failed to update field");
      }
    } catch (err) {
      console.error("Error updating client field:", err);
      toast.error("Connection error");
    }
    setInlineEditing(null);
  };

  useEffect(() => {
    fetchClients();
  }, []);

  const [pendingCounts, setPendingCounts] = useState<Record<string, number>>({});
  const [clientMaxDates, setClientMaxDates] = useState<Record<string, Date>>({});
  const [clientProjects, setClientProjects] = useState<Record<string, any[]>>({});
  const [calendarSettings, setCalendarSettings] = useState<Record<string, any>>({});
  const currentMonthYear = `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, "0")}`;

  useEffect(() => {
    const fetchCalendarSettingsForMonth = async () => {
      try {
        const res = await fetch(`${API_URL}/content-calendar-settings/all?monthYear=${calendarFilterMonth}`);
        if (res.ok) {
          const settingsList = await res.json();
          const settingsMap: Record<string, any> = {};
          settingsList.forEach((s: any) => {
            settingsMap[s.clientId] = s;
          });
          setCalendarSettings(settingsMap);
        }
      } catch (err) {
        console.error(err);
      }
    };
    fetchCalendarSettingsForMonth();
  }, [calendarFilterMonth]);

  const fetchClients = async () => {
    setIsLoading(true);
    try {
      const [res, ccRes, pRes, settingsRes, empRes] = await Promise.all([
        fetch(`${API_URL}/clients`),
        fetch(`${API_URL}/content-calendar/all`),
        fetch(`${API_URL}/projects${user ? `?userId=${user.id}&role=${user.role}` : ''}`),
        fetch(`${API_URL}/content-calendar-settings/all?monthYear=${calendarFilterMonth}`),
        fetch(`${API_URL}/employees`)
      ]);
      
      let clientsData = [];
      if (res.ok) {
        clientsData = await res.json();
      }

      let maxDatesLocal: Record<string, Date> = {};
      if (ccRes.ok) {
        const entries = await ccRes.json();
        setCalendarEntries(entries);
        const counts: Record<string, number> = {};
        entries.forEach((entry: any) => {
          let pending = 0;
          const isPost = entry.postReel === 'Post';
          const isStory = entry.postReel === 'Story';
          
          const isUserAssigned = (stageAssigneeId: string | null | undefined) => {
            if (!isEmployeeOrIntern || !user?.id) return true;
            return stageAssigneeId === user.id;
          };

          if (!isPost && !isStory && entry.scriptDate && entry.scriptDate !== '-' && !entry.scriptLink && isUserAssigned(entry.assignedScriptwriterId)) pending++;
          if (!isPost && !isStory && entry.shootDate && entry.shootDate !== '-' && (!entry.shootLink || entry.shootLink === '-') && isUserAssigned(entry.assignedShooterId)) pending++;
          
          const captionDate = entry.captionDate || entry.editingStart;
          if (!isStory && captionDate && captionDate !== '-' && !entry.caption && entry.caption !== '-' && isUserAssigned(entry.assignedCaptionWriterId)) pending++;
          
          const thumbnailDate = entry.thumbnailDate || entry.editingStart;
          if (!isPost && !isStory && thumbnailDate && thumbnailDate !== '-' && !entry.thumbnailLink && entry.thumbnailLink !== '-' && isUserAssigned(entry.assignedThumbnailDesignerId)) pending++;
          
          const isEditingPending = entry.editingStart && entry.editingStart !== '-' && (isPost ? !entry.finalPostLink : !entry.finalReelLink);
          const editorId = isPost ? entry.assignedPostDesignerId : entry.assignedReelEditorId;
          if (isEditingPending && isUserAssigned(editorId)) pending++;
          
          if (entry.approval && entry.approval !== '-' && entry.isApproved !== 'Yes' && isUserAssigned(entry.assignedApproverId)) pending++;
          if (!isStory && entry.postingDate && entry.postingDate !== '-' && !entry.postingLinkOfIg && entry.postingLinkOfIg !== '-' && isUserAssigned(entry.assignedPosterId)) pending++;

          if (pending > 0) {
            const key = entry.projectId || entry.clientId;
            counts[key] = (counts[key] || 0) + pending;
            if (entry.clientId && entry.projectId && entry.clientId !== entry.projectId) {
              counts[entry.clientId] = (counts[entry.clientId] || 0) + pending;
            }
          }

          if (entry.postingDate) {
            const d = new Date(entry.postingDate);
            if (!isNaN(d.getTime())) {
              const dateKey = entry.projectId || entry.clientId;
              if (!maxDatesLocal[dateKey] || d > maxDatesLocal[dateKey]) {
                maxDatesLocal[dateKey] = d;
              }
            }
          }
        });
        setPendingCounts(counts);
        setClientMaxDates(maxDatesLocal);
      }
      
      if (settingsRes && settingsRes.ok) {
        const settingsList = await settingsRes.json();
        const settingsMap: Record<string, any> = {};
        settingsList.forEach((s: any) => {
          settingsMap[s.clientId] = s;
        });
        setCalendarSettings(settingsMap);
      }

      if (pRes.ok) {
        const projects = await pRes.json();
        const projectMap: Record<string, any[]> = {};
        projects.forEach((p: any) => {
          if (p.clientId && p.department === 'Creative') {
            if (!projectMap[p.clientId]) {
              projectMap[p.clientId] = [];
            }
            projectMap[p.clientId].push(p);
          }
        });
        setClientProjects(projectMap);
        
        // Filter for Creative department AND must have a creative project
        const validClientIds = new Set(Object.keys(projectMap));
        let filteredClients = clientsData.filter((c: any) => c.department?.includes("Creative") && (canViewAllClients || validClientIds.has(c.id)));
        filteredClients.sort((a: any, b: any) => {
          const dateA = maxDatesLocal[a.id] ? maxDatesLocal[a.id].getTime() : Infinity;
          const dateB = maxDatesLocal[b.id] ? maxDatesLocal[b.id].getTime() : Infinity;
          return dateA - dateB;
        });
        setClients(filteredClients);
      } else {
        let filteredClients = clientsData.filter((c: any) => c.department?.includes("Creative"));
        filteredClients.sort((a: any, b: any) => {
          const dateA = maxDatesLocal[a.id] ? maxDatesLocal[a.id].getTime() : Infinity;
          const dateB = maxDatesLocal[b.id] ? maxDatesLocal[b.id].getTime() : Infinity;
          return dateA - dateB;
        });
        setClients(filteredClients);
      }

      if (empRes.ok) {
        const emps = await empRes.json();
        setCreativeEmployees(emps); // No longer filtering, user requested all employees
      }
    } catch (err) {
      console.error("Error fetching clients:", err);
      toast.error("Failed to load creative clients");
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = async (formData: ClientFormData) => {
    setIsSubmitting(true);
    try {
      const url = editingClient 
        ? `${API_URL}/clients/${editingClient.id}` 
        : `${API_URL}/clients`;
      const method = editingClient ? "PUT" : "POST";

      const payload = {
        ...formData,
        department: "Creative", // Ensure department is set
        performedBy: user?.id,
        userName: user?.name || `${user?.firstName} ${user?.lastName}`,
      };

      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        setModalOpen(false);
        fetchClients();
        setEditingClient(null);
        toast.success(editingClient ? "Client updated successfully" : "Client added successfully");
      } else {
        const error = await res.json();
        toast.error(error.detail || "Failed to save client");
      }
    } catch (err) {
      console.error("Error saving client:", err);
      toast.error("Connection error");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    const isConfirmed = await confirm({
      title: "Confirm Action",
      message: "Are you sure you want to delete this creative client?",
      destructive: true,
      confirmText: "Confirm"
    });
    if (!isConfirmed) return;

    try {
      const res = await fetch(`${API_URL}/clients/${id}`, {
        method: "DELETE",
      });
      if (res.ok) {
        fetchClients();
        toast.success("Client deleted");
      }
    } catch (err) {
      console.error("Error deleting client:", err);
    }
  };

  const handleFollowupCompleteWithRemark = async () => {
    if (!followupRemarkClient) return;
    try {
      const proj = followupRemarkProject;
      if (followupRemarkText.trim()) {
        await fetch(`${API_URL}/task-logs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "Follow-up Completed",
            details: `Remark: ${followupRemarkText}`,
            clientId: followupRemarkClient.id,
            projectId: proj?.id,
            performedBy: user?.id,
            userName: user?.name || `${user?.firstName} ${user?.lastName}`,
          })
        });
      }
      
      const today = new Date().toISOString().split('T')[0];
      const projectId = proj?.id;
      
      if (projectId) {
        const res = await fetch(`${API_URL}/projects/${projectId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 
            lastFollowupDate: today,
            performedBy: user?.id,
            userName: user?.name || `${user?.firstName} ${user?.lastName}`,
          }),
        });
        if (res.ok) {
          toast.success("Follow-up completed");
          setFollowupRemarkOpen(false);
          fetchClients();
        } else {
          toast.error("Failed to mark follow-up");
        }
      } else {
        toast.error("No active project found for this client to mark follow-up.");
      }
    } catch (err) {
      console.error("Error updating follow-up:", err);
      toast.error("An error occurred");
    }
  };

  const handleSaveTeamAssignment = async () => {
    if (!assignTeamClient) return;
    const project = assignTeamProject;
    if (!project) {
      toast.error("No creative project found for this client");
      return;
    }
    
    const scriptwriter = creativeEmployees.find(e => e.id === scriptwriterId);
    const reelEditor = creativeEmployees.find(e => e.id === reelEditorId);
    const postDesigner = creativeEmployees.find(e => e.id === postDesignerId);
    const shooter = creativeEmployees.find(e => e.id === shooterId);
    const approver = creativeEmployees.find(e => e.id === approverId);
    const poster = creativeEmployees.find(e => e.id === posterId);
    const captionWriter = creativeEmployees.find(e => e.id === captionWriterId);
    const thumbnailDesigner = creativeEmployees.find(e => e.id === thumbnailDesignerId);
    const whatsappGroupCreator = creativeEmployees.find(e => e.id === whatsappGroupCreatorId);
    const greetingsMsgSender = creativeEmployees.find(e => e.id === greetingsMsgSenderId);
    const meetingsAssignee = creativeEmployees.find(e => e.id === meetingsAssigneeId);
    const contentCalendarCreator = creativeEmployees.find(e => e.id === contentCalendarCreatorId);
    const followupAssignee = creativeEmployees.find(e => e.id === followupAssigneeId);
    
    try {
      const res = await fetch(`${API_URL}/projects/${project.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          assignedScriptwriterId: scriptwriterId === "none" ? null : scriptwriterId || null,
          assignedScriptwriterName: scriptwriterId === "none" ? null : scriptwriter?.name || null,
          assignedReelEditorId: reelEditorId === "none" ? null : reelEditorId || null,
          assignedReelEditorName: reelEditorId === "none" ? null : reelEditor?.name || null,
          assignedPostDesignerId: postDesignerId === "none" ? null : postDesignerId || null,
          assignedPostDesignerName: postDesignerId === "none" ? null : postDesigner?.name || null,
          assignedShooterId: shooterId === "none" ? null : shooterId || null,
          assignedShooterName: shooterId === "none" ? null : shooter?.name || null,
          assignedApproverId: approverId === "none" ? null : approverId || null,
          assignedApproverName: approverId === "none" ? null : approver?.name || null,
          assignedPosterId: posterId === "none" ? null : posterId || null,
          assignedPosterName: posterId === "none" ? null : poster?.name || null,
          assignedCaptionWriterId: captionWriterId === "none" ? null : captionWriterId || null,
          assignedCaptionWriterName: captionWriterId === "none" ? null : captionWriter?.name || null,
          assignedThumbnailDesignerId: thumbnailDesignerId === "none" ? null : thumbnailDesignerId || null,
          assignedThumbnailDesignerName: thumbnailDesignerId === "none" ? null : thumbnailDesigner?.name || null,
          assignedWhatsappGroupCreatorId: whatsappGroupCreatorId === "none" ? null : whatsappGroupCreatorId || null,
          assignedWhatsappGroupCreatorName: whatsappGroupCreatorId === "none" ? null : whatsappGroupCreator?.name || null,
          assignedGreetingsMsgSenderId: greetingsMsgSenderId === "none" ? null : greetingsMsgSenderId || null,
          assignedGreetingsMsgSenderName: greetingsMsgSenderId === "none" ? null : greetingsMsgSender?.name || null,
          assignedMeetingsAssigneeId: meetingsAssigneeId === "none" ? null : meetingsAssigneeId || null,
          assignedMeetingsAssigneeName: meetingsAssigneeId === "none" ? null : meetingsAssignee?.name || null,
          assignedContentCalendarCreatorId: contentCalendarCreatorId === "none" ? null : contentCalendarCreatorId || null,
          assignedContentCalendarCreatorName: contentCalendarCreatorId === "none" ? null : contentCalendarCreator?.name || null,
          assignedFollowUpId: followupAssigneeId === "none" ? null : followupAssigneeId || null,
          assignedFollowUpName: followupAssigneeId === "none" ? null : followupAssignee?.name || null,
          performedBy: user?.id,
          userName: user?.name || `${user?.firstName} ${user?.lastName}`,
        }),
      });
      if (res.ok) {
        toast.success("Team assigned successfully");
        setAssignTeamOpen(false);
        fetchClients();
      } else {
        toast.error("Failed to assign team");
      }
    } catch (err) {
      console.error("Error assigning team:", err);
      toast.error("An error occurred");
    }
  };

  const handleSaveFollowupConfig = async () => {
    if (!followupConfigClient) return;
    try {
      const res = await fetch(`${API_URL}/clients/${followupConfigClient.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          followupType: followupTypeInput,
          followupIntervalDays: parseInt(followupIntervalInput) || null,
          followupDaysOfWeek: followupDaysOfWeekInput,
          followupDatesOfMonth: followupDatesOfMonthInput,
          lastFollowupDate: followupLastDateInput || null,
          performedBy: user?.id,
          userName: user?.name || `${user?.firstName} ${user?.lastName}`,
        }),
      });
      if (res.ok) {
        toast.success("Follow-up configuration saved");
        setFollowupConfigOpen(false);
        fetchClients();
      } else {
        toast.error("Failed to save follow-up configuration");
      }
    } catch (err) {
      console.error("Error saving follow-up config:", err);
      toast.error("An error occurred");
    }
  };

  const handleSaveFeedbackConfig = async () => {
    if (!feedbackConfigProject) return;
    try {
      const res = await fetch(`${API_URL}/projects/${feedbackConfigProject.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          feedbackType: feedbackTypeInput,
          feedbackIntervalDays: parseInt(feedbackIntervalInput) || null,
          feedbackDaysOfWeek: feedbackDaysOfWeekInput,
          feedbackDatesOfMonth: feedbackDatesOfMonthInput,
          lastFeedbackDate: feedbackLastDateInput || null,
          nextFeedbackDate: feedbackNextDateInput || null,
          performedBy: user?.id,
          userName: user?.name || `${user?.firstName} ${user?.lastName}`,
        }),
      });
      if (res.ok) {
        toast.success("Feedback configuration saved");
        setFeedbackConfigOpen(false);
        fetchClients();
      } else {
        toast.error("Failed to save feedback configuration");
      }
    } catch (err) {
      console.error("Error saving feedback config:", err);
      toast.error("An error occurred");
    }
  };

  const clientProjectRows = useMemo(() => {
    const rows: Array<{ client: any; project: any }> = [];
    clients.forEach((client) => {
      const projs = clientProjects[client.id] || [];
      if (projs.length === 0) {
        rows.push({ client, project: null });
      } else {
        projs.forEach((project) => {
          rows.push({ client, project });
        });
      }
    });
    return rows;
  }, [clients, clientProjects]);

  const filteredRows = useMemo(() => {
    return clientProjectRows.filter(({ client: c, project: p }: { client: any; project: any }) => {
      if (!canViewAllClients && user?.id) {
        const proj = p || {};
        const hasAssignedCCEntry = calendarEntries.some((entry: any) => {
          if (entry.clientId !== c.id) return false;
          return entry.assignedScriptwriterId === user.id ||
                 entry.assignedShooterId === user.id ||
                 entry.assignedCaptionWriterId === user.id ||
                 entry.assignedThumbnailDesignerId === user.id ||
                 entry.assignedReelEditorId === user.id ||
                 entry.assignedPostDesignerId === user.id ||
                 entry.assignedApproverId === user.id ||
                 entry.assignedPosterId === user.id;
        });

        const isAssigned = (proj.assignedScriptwriterId || c.assignedScriptwriterId) === user.id || 
                           (proj.assignedReelEditorId || c.assignedReelEditorId) === user.id ||
                           (proj.assignedPostDesignerId || c.assignedPostDesignerId) === user.id ||
                           (proj.assignedShooterId || c.assignedShooterId) === user.id ||
                           (proj.assignedApproverId || c.assignedApproverId) === user.id ||
                           (proj.assignedPosterId || c.assignedPosterId) === user.id ||
                           (proj.assignedCaptionWriterId || c.assignedCaptionWriterId) === user.id ||
                           (proj.assignedThumbnailDesignerId || c.assignedThumbnailDesignerId) === user.id ||
                           (proj.assignedWhatsappGroupCreatorId || c.assignedWhatsappGroupCreatorId) === user.id ||
                           (proj.assignedGreetingsMsgSenderId || c.assignedGreetingsMsgSenderId) === user.id ||
                           (proj.assignedMeetingsAssigneeId || c.assignedMeetingsAssigneeId) === user.id ||
                           (proj.assignedContentCalendarCreatorId || c.assignedContentCalendarCreatorId) === user.id ||
                           (proj.assignedFollowUpId || c.assignedFollowUpId) === user.id ||
                           hasAssignedCCEntry;
        if (!isAssigned) return false;
      }

      const matchesSearch = c.companyName.toLowerCase().includes(searchTerm.toLowerCase()) || 
        c.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        c.email.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (p && p.title.toLowerCase().includes(searchTerm.toLowerCase()));
        
      if (!matchesSearch) return false;

      const projectStatus = p?.status || "";
      const isOnHold = projectStatus.toLowerCase() === "on-hold";
      if (isOnHold && masterFilter !== "on-hold") return false;
      const isFollowupDue = p?.nextFollowupDate && new Date(p.nextFollowupDate) <= new Date();
      
      const hasPendingWork = pendingCounts[c.id] > 0;

      switch(masterFilter) {
        case 'whatsapp-submitted': return !!c.whatsappGroup;
        case 'whatsapp-pending': return !c.whatsappGroup;
        case 'greetings-sent': return !!c.greetingsMsgSent;
        case 'greetings-pending': return !c.greetingsMsgSent;
        case 'followup-due': return !!isFollowupDue;
        case 'active': return p ? !isOnHold : true;
        case 'on-hold': return isOnHold;
        case 'festival-post': return (p?.festivalPost === "Yes") || c.festivalPost === "Yes";
        case 'pending-work': return hasPendingWork;
        case 'meeting-done': return c.meetings && c.meetings.length > 0;
        case 'meeting-not-done': return !c.meetings || c.meetings.length === 0;
        case 'approval-pending': return !calendarSettings[c.id]?.approvalStatus || calendarSettings[c.id]?.approvalStatus === "Pending";
        case 'approval-approved': return calendarSettings[c.id]?.approvalStatus === "Approved by Client";
        case 'approval-changes': return calendarSettings[c.id]?.approvalStatus === "Changes Requested";
        case 'approval-rejected': return calendarSettings[c.id]?.approvalStatus === "Rejected";
        default: return true;
      }
    }).filter(({ client: c, project: p }: { client: any; project: any }) => {
      if (creativeFilter !== "all") {
        const proj = p || {};
        const isAssigned = (proj.assignedScriptwriterId || c.assignedScriptwriterId) === creativeFilter || 
                           (proj.assignedReelEditorId || c.assignedReelEditorId) === creativeFilter ||
                           (proj.assignedPostDesignerId || c.assignedPostDesignerId) === creativeFilter ||
                           (proj.assignedShooterId || c.assignedShooterId) === creativeFilter ||
                           (proj.assignedApproverId || c.assignedApproverId) === creativeFilter ||
                           (proj.assignedPosterId || c.assignedPosterId) === creativeFilter ||
                           (proj.assignedCaptionWriterId || c.assignedCaptionWriterId) === creativeFilter ||
                           (proj.assignedThumbnailDesignerId || c.assignedThumbnailDesignerId) === creativeFilter ||
                           (proj.assignedWhatsappGroupCreatorId || c.assignedWhatsappGroupCreatorId) === creativeFilter ||
                           (proj.assignedGreetingsMsgSenderId || c.assignedGreetingsMsgSenderId) === creativeFilter ||
                           (proj.assignedMeetingsAssigneeId || c.assignedMeetingsAssigneeId) === creativeFilter ||
                           (proj.assignedContentCalendarCreatorId || c.assignedContentCalendarCreatorId) === creativeFilter ||
                           (proj.assignedFollowUpId || c.assignedFollowUpId) === creativeFilter;
        if (!isAssigned) return false;
      }

      if (calendarFilterStatus === "created") {
        if (!calendarSettings[c.id]) return false;
      } else if (calendarFilterStatus === "not-created") {
        if (calendarSettings[c.id]) return false;
      }

      return true;
    });
  }, [clientProjectRows, isEmployeeOrIntern, user, searchTerm, masterFilter, creativeFilter, calendarFilterStatus, pendingCounts, calendarSettings, calendarEntries]);

  return (
    <div className="space-y-6">
      <style dangerouslySetInnerHTML={{ __html: noScrollbarStyle }} />
      <PageHeader
        title="Social Media Management"
        description="Streamline client deliverables, track campaign progress, and centralize SMM communications."
      />

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsContent value="projects" className="space-y-6 m-0">
          <div className="flex flex-col md:flex-row items-center gap-4 bg-white p-4 rounded-xl border border-slate-100 shadow-sm">
        <div className="relative flex-1 w-full flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <Input 
              placeholder="Search by client name, email..." 
              className="pl-10 h-10 border-slate-200 focus:border-brand-teal focus:ring-brand-teal"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" className={`h-10 shrink-0 gap-2 border-slate-200 ${creativeFilter !== 'all' ? 'bg-brand-teal/5 text-brand-teal border-brand-teal/30' : ''}`}>
                <SlidersHorizontal className="w-4 h-4" />
                Advanced Filters
                {creativeFilter !== 'all' && (
                  <Badge variant="secondary" className="ml-1 px-1.5 py-0 text-[10px] bg-brand-teal text-white">Active</Badge>
                )}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-80 p-0 shadow-xl border-slate-200" align="start">
              <div className="bg-slate-50/80 px-4 py-3 border-b border-slate-100 flex items-center justify-between">
                <h4 className="font-semibold text-slate-700 text-sm">Master Filters</h4>
                {creativeFilter !== 'all' && (
                  <button onClick={() => setCreativeFilter('all')} className="text-xs text-slate-400 hover:text-rose-500 font-medium">Clear All</button>
                )}
              </div>
                <div className="p-4 space-y-5">
                <div className="space-y-3">
                  <Label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Assigned Team Member</Label>
                  <Select value={creativeFilter} onValueChange={setCreativeFilter}>
                    <SelectTrigger className="w-full h-10 border-slate-200">
                      <SelectValue placeholder="All Creative Team" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all" className="font-medium">All Team Members</SelectItem>
                      {creativeEmployees.map(emp => (
                        <SelectItem key={emp.id} value={emp.id}>
                          <div className="flex items-center gap-2">
                            <Avatar className="w-5 h-5">
                              <AvatarFallback className="text-[9px] bg-brand-teal/10 text-brand-teal">{emp.name?.substring(0,2).toUpperCase() || emp.firstName?.substring(0,2).toUpperCase()}</AvatarFallback>
                            </Avatar>
                            <span>{emp.name || `${emp.firstName} ${emp.lastName}`}</span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </PopoverContent>
          </Popover>
        </div>

        {/* Content Calendar Filter */}
        <div className="flex items-center h-10 bg-white rounded-md border border-slate-200 shrink-0 w-full md:w-auto overflow-hidden text-sm transition-all focus-within:ring-1 focus-within:ring-brand-teal focus-within:border-brand-teal">
          <div className="flex items-center gap-2 px-3 bg-slate-50/80 border-r border-slate-200 h-full text-slate-600 shrink-0">
            <CalendarClock className="h-4 w-4" />
            <span className="font-medium hidden xl:inline">Calendar</span>
          </div>
          <input 
            type="month" 
            value={calendarFilterMonth} 
            onChange={(e) => setCalendarFilterMonth(e.target.value)}
            className="h-full bg-transparent border-none text-slate-700 outline-none cursor-pointer w-[135px] px-3 focus:ring-0 shrink-0"
          />
          <div className="w-px h-6 bg-slate-200 shrink-0"></div>
          <Select value={calendarFilterStatus} onValueChange={setCalendarFilterStatus}>
            <SelectTrigger className="w-[115px] h-full border-none bg-transparent shadow-none text-slate-700 focus:ring-0 rounded-none px-3 hover:bg-slate-50 shrink-0">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="created">Created</SelectItem>
              <SelectItem value="not-created">Not Created</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {!isRealAdmin && (
          <Button 
            variant="default" 
            className="h-10 shrink-0 gap-2 bg-brand-teal text-white hover:bg-brand-teal-light"
            onClick={() => setActiveTab('progress')}
          >
            Daily Progress
          </Button>
        )}
        {(isAdmin || (user?.designation?.toLowerCase() === 'team leader' || user?.designation?.toLowerCase() === 'head') || user?.designation?.toLowerCase() === 'hr') && <OtherWorkDialog />}
        <Button onClick={() => router.push('/work-management/smm/common/feedback')} className="h-10 bg-slate-100 hover:bg-slate-200 text-slate-700 gap-2 w-full md:w-auto shrink-0 border border-slate-200">
          <ClipboardList className="w-4 h-4" />
          View Common Forms
        </Button>
      </div>

      <div className="w-full mb-6 overflow-x-auto pb-2 no-scrollbar">
        <div className="inline-flex items-center gap-1 w-max bg-slate-100/70 p-1 rounded-xl shadow-inner border border-slate-200/60">
          {[
            { value: "all", label: "All Clients" },
            { value: "active", label: "Active Projects" },
            { value: "work-group", label: "Work" },
            { value: "reviews", label: "Client Reviews" },
            { value: "followup-due", label: "Follow-up Due" },
            { value: "on-hold", label: "On Hold" },
          ].map(filter => {
            if (filter.value === "work-group") {
              return (
                <DropdownMenu key="work-group">
                  <DropdownMenuTrigger asChild>
                    <button
                      className={`px-4 py-2 rounded-lg text-sm font-bold flex items-center gap-1.5 transition-all whitespace-nowrap ${
                        ["pending-work", "todays-work", "upcoming-work", "completed-work"].includes(masterFilter)
                          ? "bg-white text-brand-teal shadow-sm border border-slate-200/50" 
                          : "text-slate-500 hover:text-slate-800 hover:bg-slate-200/50 border border-transparent"
                      }`}
                    >
                      Work
                      <ChevronDown className="w-3.5 h-3.5 opacity-70" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-48">
                    <DropdownMenuItem onClick={() => setMasterFilter("pending-work")} className="font-medium cursor-pointer">
                      Pending Work
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setMasterFilter("todays-work")} className="font-medium cursor-pointer">
                      Today's Work
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setMasterFilter("upcoming-work")} className="font-medium cursor-pointer">
                      Upcoming Work
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setMasterFilter("completed-work")} className="font-medium cursor-pointer">
                      Completed Work
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              );
            }
            return (
              <button
                key={filter.value}
                onClick={() => setMasterFilter(filter.value)}
                className={`px-4 py-2 rounded-lg text-sm font-bold flex items-center gap-1.5 transition-all whitespace-nowrap ${
                  masterFilter === filter.value 
                    ? "bg-white text-brand-teal shadow-sm border border-slate-200/50" 
                    : "text-slate-500 hover:text-slate-800 hover:bg-slate-200/50 border border-transparent"
                }`}
              >
                {filter.label}
              </button>
            );
          })}

          <div className="w-px h-6 bg-slate-200 mx-1"></div>

          <button
            onClick={() => setMasterFilter("festival-post")}
            className={`px-4 py-2 rounded-lg text-sm font-bold flex items-center gap-1.5 transition-all whitespace-nowrap ${
              masterFilter === "festival-post" 
                ? "bg-white text-brand-teal shadow-sm border border-slate-200/50" 
                : "text-slate-500 hover:text-slate-800 hover:bg-slate-200/50 border border-transparent"
            }`}
          >
            Festival Post
          </button>



          <Select 
            value={
              ["whatsapp-submitted", "whatsapp-pending", "greetings-sent", "greetings-pending"].includes(masterFilter) 
                ? masterFilter 
                : ""
            } 
            onValueChange={(val) => {
              if (val) setMasterFilter(val);
            }}
          >
            <SelectTrigger 
              className={`w-[140px] h-9 border-none font-bold rounded-lg ${
                ["whatsapp-submitted", "whatsapp-pending", "greetings-sent", "greetings-pending"].includes(masterFilter)
                  ? "bg-white text-brand-teal shadow-sm" 
                  : "bg-transparent text-slate-500 hover:text-slate-800 hover:bg-slate-200/50"
              }`}
            >
              <SelectValue placeholder="WhatsApp" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="whatsapp-submitted">Group Created</SelectItem>
              <SelectItem value="whatsapp-pending">Group Pending</SelectItem>
              <SelectItem value="greetings-sent">Greetings Sent</SelectItem>
              <SelectItem value="greetings-pending">Greetings Pending</SelectItem>
            </SelectContent>
          </Select>

          <Select 
            value={
              ["meeting-done", "meeting-not-done"].includes(masterFilter) 
                ? masterFilter 
                : ""
            } 
            onValueChange={(val) => {
              if (val) setMasterFilter(val);
            }}
          >
            <SelectTrigger 
              className={`w-[130px] h-9 border-none font-bold rounded-lg ${
                ["meeting-done", "meeting-not-done"].includes(masterFilter)
                  ? "bg-white text-brand-teal shadow-sm" 
                  : "bg-transparent text-slate-500 hover:text-slate-800 hover:bg-slate-200/50"
              }`}
            >
              <SelectValue placeholder="Meetings" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="meeting-done">Meeting Done</SelectItem>
              <SelectItem value="meeting-not-done">Meeting Pending</SelectItem>
            </SelectContent>
          </Select>

          <Select 
            value={
              ["approval-pending", "approval-approved", "approval-changes", "approval-rejected"].includes(masterFilter) 
                ? masterFilter 
                : ""
            } 
            onValueChange={(val) => {
              if (val) setMasterFilter(val);
            }}
          >
            <SelectTrigger 
              className={`w-[130px] h-9 border-none font-bold rounded-lg ${
                ["approval-pending", "approval-approved", "approval-changes", "approval-rejected"].includes(masterFilter)
                  ? "bg-white text-brand-teal shadow-sm" 
                  : "bg-transparent text-slate-500 hover:text-slate-800 hover:bg-slate-200/50"
              }`}
            >
              <SelectValue placeholder="CC Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="approval-pending">Pending</SelectItem>
              <SelectItem value="approval-approved">Approved by Client</SelectItem>
              <SelectItem value="approval-changes">Changes Requested</SelectItem>
              <SelectItem value="approval-rejected">Rejected</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {isLoading ? (
        <div className="py-20 text-center flex flex-col items-center gap-3">
          <Loader2 className="w-8 h-8 text-brand-teal animate-spin" />
          <p className="text-sm text-slate-500 font-medium">Fetching dashboard...</p>
        </div>
      ) : ['pending-work', 'todays-work', 'upcoming-work', 'completed-work', 'digital-marketing'].includes(masterFilter) ? (
        <PendingWorkEmbedded 
          type={masterFilter === 'digital-marketing' ? 'pending-work' : masterFilter as any} 
          defaultTaskType={masterFilter === 'digital-marketing' ? 'digital-marketing' : 'smm-all'}
        />
      ) : masterFilter === 'reviews' ? (
        <FeedbackReviewsEmbedded />
      ) : filteredRows.length > 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden shadow-md min-h-[calc(100vh-260px)]" data-slot="table-container">
          <div className="overflow-x-auto no-scrollbar">
            <table className="w-full text-left text-sm text-slate-600">
              <thead className="bg-slate-50/80 border-b border-slate-200 text-slate-500 text-xs uppercase tracking-wider font-semibold">
                <tr>
                  <th className="px-6 py-4 whitespace-nowrap sticky left-0 z-20 bg-slate-50 shadow-[1px_0_0_0_#e2e8f0] min-w-[250px] w-[250px] max-w-[250px]">Company</th>
                  <th className="px-6 py-4 text-center whitespace-nowrap sticky left-[250px] z-20 bg-slate-50 shadow-[2px_0_5px_-2px_rgba(0,0,0,0.1)] min-w-[120px] w-[120px] max-w-[120px]">End Date</th>
                  <th className="px-6 py-4 whitespace-nowrap">Project</th>
                  <th className="px-6 py-4 whitespace-nowrap">Contact Name</th>
                  <th className="px-6 py-4 whitespace-nowrap">Phone</th>
                  <th className="px-6 py-4 whitespace-nowrap">Email</th>
                  <th className="px-6 py-4 whitespace-nowrap">Services</th>
                  <th className="px-6 py-4 text-center whitespace-nowrap">Status</th>
                  <th className="px-6 py-4 text-right whitespace-nowrap sticky right-0 z-20 bg-slate-50 shadow-[-1px_0_0_0_#e2e8f0]">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredRows.map(({ client, project }: { client: any; project: any }) => (
                  <tr key={`${client.id}-${project?.id || 'no-project'}`} className="hover:bg-slate-50/50 transition-colors group">
                    <td className="px-6 py-4 whitespace-normal sticky left-0 z-10 bg-white group-hover:bg-slate-50 shadow-[1px_0_0_0_#e2e8f0] transition-colors min-w-[250px] w-[250px] max-w-[250px] overflow-hidden">
                      <div className="flex flex-col gap-1.5 items-start">
                        <div className="flex items-center gap-3">
                          <div 
                            className="font-semibold text-brand-teal text-base hover:text-brand-teal/80 transition-colors cursor-pointer pl-2"
                            onClick={() => router.push(`/work-management/smm/${client.id}${project ? `?projectId=${project.id}` : ''}`)}
                          >
                            {project?.title || project?.name ? (
                              <div className="flex flex-col gap-0.5">
                                <span className="underline underline-offset-2 leading-tight">{project.title || project.name}</span>
                                <span className="text-xs text-slate-500 font-medium no-underline">{client.companyName}</span>
                              </div>
                            ) : (
                              <span className="underline underline-offset-2">{client.companyName || client.name || "N/A"}</span>
                            )}
                          </div>
                          {(() => {
                            const clientProjs = clientProjects[client.id] || [];
                            const oldestProject = clientProjs.reduce((oldest: any, p: any) => {
                              if (!oldest) return p;
                              return (p._id || p.id) < (oldest._id || oldest.id) ? p : oldest;
                            }, null);
                            const isOldestProject = oldestProject?.id === project?.id;
                            const legacyCount = isOldestProject || !project ? (pendingCounts[client.id] || 0) : 0;
                            const totalPending = (project ? (pendingCounts[project.id] || 0) : 0) + legacyCount;
                            
                            if (totalPending > 0) {
                              return (
                                <Badge 
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    router.push(`/work-management/smm/pending?client=${client.id}${project ? `&projectId=${project.id}` : ''}`);
                                  }}
                                  className="bg-rose-700 hover:bg-rose-800 text-[10px] px-1.5 py-0 cursor-pointer"
                                >
                                  {totalPending} Pending
                                </Badge>
                              );
                            }
                            return null;
                          })()}
                        </div>
                      </div>
                      {calendarSettings[client.id] && calendarSettings[client.id].approvalStatus && calendarSettings[client.id].approvalStatus !== "Approved by Client" && calendarSettings[client.id].approvalStatus !== "Pending" && (
                        <div className="mt-2 text-[10px] font-medium text-rose-600 bg-rose-50 px-2 py-1 rounded-md border border-rose-100 inline-block w-full max-w-full">
                          <span className="font-semibold">{calendarSettings[client.id].approvalStatus}</span>
                          {calendarSettings[client.id].statusLogs && calendarSettings[client.id].statusLogs.length > 0 && calendarSettings[client.id].statusLogs[calendarSettings[client.id].statusLogs.length - 1].reason && (
                            <div className="text-rose-500 font-normal truncate mt-0.5" title={calendarSettings[client.id].statusLogs[calendarSettings[client.id].statusLogs.length - 1].reason}>
                              Reason: {calendarSettings[client.id].statusLogs[calendarSettings[client.id].statusLogs.length - 1].reason}
                            </div>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="px-6 py-4 text-center whitespace-nowrap sticky left-[250px] z-10 bg-white group-hover:bg-slate-50 shadow-[2px_0_5px_-2px_rgba(0,0,0,0.1)] transition-colors min-w-[120px] w-[120px] max-w-[120px]">
                      {(() => {
                        const clientProjs = clientProjects[client.id] || [];
                        const oldestProject = clientProjs.reduce((oldest: any, p: any) => {
                          if (!oldest) return p;
                          return (p._id || p.id) < (oldest._id || oldest.id) ? p : oldest;
                        }, null);
                        const isOldestProject = oldestProject?.id === project?.id;
                        const legacyDate = isOldestProject || !project ? clientMaxDates[client.id] : null;
                        const projectDate = project ? clientMaxDates[project.id] : null;
                        const finalDate = (projectDate && legacyDate) ? (projectDate > legacyDate ? projectDate : legacyDate) : (projectDate || legacyDate);
                        
                        if (finalDate) {
                          return (
                            <div className="inline-flex">
                              <Badge variant="outline" className="bg-orange-50 text-orange-700 border-orange-200 shadow-sm text-xs font-semibold px-2.5 py-0.5 rounded-md">
                                {finalDate.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}
                              </Badge>
                            </div>
                          );
                        }
                        return <span className="text-slate-400 italic text-xs">N/A</span>;
                      })()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex flex-col items-start gap-1">
                        <span className="text-slate-700 text-sm font-medium">
                          {project?.title || <span className="text-slate-400 italic font-normal">No active project</span>}
                        </span>
                        {project?.status?.toLowerCase() === "on-hold" && (
                          <Badge variant="outline" className="text-[10px] bg-red-50 text-red-600 border-red-200 px-1 py-0 shadow-none font-semibold">ON HOLD</Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2 text-slate-700">
                        <div className="bg-slate-100 p-1 rounded-md text-slate-500">
                          <User className="w-3.5 h-3.5" /> 
                        </div>
                        <span 
                          className="cursor-pointer hover:text-brand-teal font-medium"
                          onClick={() => setInlineEditing({ id: client.id, field: 'name' })}
                        >
                          {client.name || "N/A"}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2 text-slate-500 text-xs">
                        <div className="bg-slate-100 p-1 rounded-md text-slate-500">
                          <Phone className="w-3 h-3" /> 
                        </div>
                        <span 
                          className={`truncate max-w-[180px] ${!isEmployeeOrIntern ? 'cursor-pointer hover:text-brand-teal transition-colors' : ''}`}
                          onClick={() => !isEmployeeOrIntern && setInlineEditing({ id: client.id, field: 'phone' })}
                        >
                          {client.phone || "N/A"}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2 text-slate-500 text-xs">
                        <div className="bg-slate-100 p-1 rounded-md text-slate-500">
                          <Mail className="w-3 h-3" /> 
                        </div>
                        <span 
                          className={`truncate max-w-[180px] ${!isEmployeeOrIntern ? 'cursor-pointer hover:text-brand-teal transition-colors' : ''}`}
                          onClick={() => !isEmployeeOrIntern && setInlineEditing({ id: client.id, field: 'email' })}
                        >
                          {client.email || "N/A"}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-3">
                        <div 
                          className={`line-clamp-2 max-w-[220px] font-medium text-slate-700 ${!isEmployeeOrIntern && !project ? 'cursor-pointer hover:text-brand-teal transition-colors' : ''}`}
                          onClick={() => !isEmployeeOrIntern && !project && setInlineEditing({ id: client.id, field: 'services' })}
                        >
                          {(project ? project.services : client.services) || "N/A"}
                        </div>
                        {((project ? project.festivalPost : client.festivalPost) === "Yes") && (
                          <Badge variant="outline" className="bg-amber-50 text-amber-600 border-amber-200/60 font-medium px-2 py-0.5 shadow-sm">
                            Festival Post
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-center whitespace-nowrap">
                      <div className="flex items-center justify-center gap-2.5">
                        {(() => {
                          const projectStatus = project?.status || "";
                          const isOnHold = projectStatus.toLowerCase() === "on-hold";
                          const displayText = isOnHold ? "ON HOLD" : "ACTIVE";
                          return (
                            <Badge className={!isOnHold ? "bg-emerald-50 text-emerald-600 border-emerald-200/60 shadow-sm font-semibold" : "bg-red-50 text-red-600 border-red-200/60 shadow-sm font-semibold"}>
                              {displayText}
                            </Badge>
                          );
                        })()}
                        {project?.nextFollowupDate && new Date(project.nextFollowupDate) <= new Date() && (
                          <Badge 
                            className="bg-rose-50 text-rose-600 border-rose-200/60 animate-pulse flex items-center gap-1.5 shadow-sm cursor-pointer hover:bg-rose-100 hover:text-rose-700 transition-colors"
                            onClick={(e) => {
                              e.stopPropagation();
                              setFollowupRemarkClient(client);
                              setFollowupRemarkProject(project);
                              setFollowupRemarkText("");
                              setFollowupRemarkOpen(true);
                            }}
                          >
                            <AlertCircle className="w-3 h-3" />
                            Follow-up Due
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-right align-middle whitespace-nowrap sticky right-0 z-10 bg-white group-hover:bg-slate-50 shadow-[-1px_0_0_0_#e2e8f0] transition-colors">
                      <div className="flex items-center justify-end gap-1.5 opacity-100 transition-all duration-200">
                        <SmmMeetingDialog 
                          client={client} 
                          onUpdate={fetchClients} 
                          userId={user?.userId} 
                          userName={user?.name} 
                        />
                        <Button 
                          variant="ghost"  
                          size="icon" 
                          className={`h-9 w-9 rounded-full ${client.whatsappGroup ? 'text-[#25D366] hover:text-[#25D366] hover:bg-[#25D366]/10' : 'text-slate-400 hover:text-[#25D366] hover:bg-[#25D366]/10'}`}
                          title={client.whatsappGroup ? "Manage WhatsApp Group" : "Add WhatsApp Group"}
                          onClick={(e) => {
                            e.stopPropagation();
                            setWaClient(client);
                            setWaDialogOpen(true);
                          }}
                        >
                          <WhatsAppIcon className="w-4.5 h-4.5" />
                        </Button>

                        {!isEmployeeOrIntern && (
                          <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button 
                              variant="ghost" 
                              size="icon" 
                              className="h-9 w-9 text-slate-400 hover:text-brand-teal hover:bg-brand-teal/10 rounded-full"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <MoreHorizontal className="w-4.5 h-4.5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-56" onClick={(e) => e.stopPropagation()}>
                            <DropdownMenuLabel className="text-xs text-slate-500 font-normal">Manage Client</DropdownMenuLabel>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem onClick={(e) => {
                              e.stopPropagation();
                              setAssignTeamClient(client);
                              setAssignTeamProject(project);
                              const p = project || {};
                              setScriptwriterId(p.assignedScriptwriterId || client.assignedScriptwriterId || "none");
                              setReelEditorId(p.assignedReelEditorId || client.assignedReelEditorId || "none");
                              setPostDesignerId(p.assignedPostDesignerId || client.assignedPostDesignerId || "none");
                              setShooterId(p.assignedShooterId || client.assignedShooterId || "none");
                              setApproverId(p.assignedApproverId || client.assignedApproverId || "none");
                              setPosterId(p.assignedPosterId || client.assignedPosterId || "none");
                              setCaptionWriterId(p.assignedCaptionWriterId || client.assignedCaptionWriterId || "none");
                              setThumbnailDesignerId(p.assignedThumbnailDesignerId || client.assignedThumbnailDesignerId || "none");
                              setWhatsappGroupCreatorId(p.assignedWhatsappGroupCreatorId || client.assignedWhatsappGroupCreatorId || "none");
                              setGreetingsMsgSenderId(p.assignedGreetingsMsgSenderId || client.assignedGreetingsMsgSenderId || "none");
                              setMeetingsAssigneeId(p.assignedMeetingsAssigneeId || client.assignedMeetingsAssigneeId || "none");
                              setContentCalendarCreatorId(p.assignedContentCalendarCreatorId || client.assignedContentCalendarCreatorId || "none");
                              setFollowupAssigneeId(p.assignedFollowUpId || client.assignedFollowUpId || "none");
                              setAssignTeamOpen(true);
                            }}>
                              <UserPlus className="w-4 h-4 mr-2" /> Assign Creative Team
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={(e) => {
                              e.stopPropagation();
                              setFollowupConfigClient(client);
                              setFollowupConfigProject(project);
                              setFollowupTypeInput(project?.followupType || client.followupType || "Interval");
                              setFollowupIntervalInput(project?.followupIntervalDays ? String(project.followupIntervalDays) : (client.followupIntervalDays ? String(client.followupIntervalDays) : ""));
                              setFollowupDaysOfWeekInput(project?.followupDaysOfWeek || client.followupDaysOfWeek || []);
                              setFollowupDatesOfMonthInput(project?.followupDatesOfMonth || client.followupDatesOfMonth || []);
                              setFollowupLastDateInput(project?.lastFollowupDate || client.lastFollowupDate || "");
                              setFollowupConfigOpen(true);
                              fetchFollowupHistory(client, project);
                            }}>
                              <CalendarClock className="w-4 h-4 mr-2" /> Follow-ups
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={(e) => {
                              e.stopPropagation();
                              const p = project;
                              if (p) {
                                setFeedbackConfigProject(p);
                                setFeedbackTypeInput(p.feedbackType || "Interval");
                                setFeedbackIntervalInput(p.feedbackIntervalDays ? String(p.feedbackIntervalDays) : "");
                                setFeedbackDaysOfWeekInput(p.feedbackDaysOfWeek || []);
                                setFeedbackDatesOfMonthInput(p.feedbackDatesOfMonth || []);
                                setFeedbackLastDateInput(p.lastFeedbackDate || "");
                                setFeedbackNextDateInput(p.nextFeedbackDate || "");
                                setFeedbackConfigOpen(true);
                              } else {
                                toast.error("No active project found for this client.");
                              }
                            }}>
                              <History className="w-4 h-4 mr-2 text-emerald-600" /> Feedback Collection
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={(e) => {
                              e.stopPropagation();
                              setReviewClient(client);
                              setReviewDialogOpen(true);
                            }}>
                              <Star className="w-4 h-4 mr-2 text-amber-500" /> Client Reviews
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={(e) => {
                              e.stopPropagation();
                              router.push(`/work-management/smm/${client.id}/feedback${project ? `?projectId=${project.id}` : ''}`);
                            }}>
                              <ClipboardList className="w-4 h-4 mr-2 text-indigo-600" /> View Forms & Feedback
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem onClick={(e) => {
                              e.stopPropagation();
                              fetchLogs(client, project);
                            }}>
                              <History className="w-4 h-4 mr-2" /> Activity Logs
                            </DropdownMenuItem>
                            <DropdownMenuItem 
                              className="text-red-600 focus:bg-red-50 focus:text-red-600"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDelete(client.id);
                              }}
                            >
                              <Trash2 className="w-4 h-4 mr-2" /> Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                          </DropdownMenu>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="py-20 text-center flex flex-col items-center gap-3">
          <div className="w-12 h-12 bg-slate-100 rounded-full flex items-center justify-center text-slate-400">
            <ClipboardList className="w-6 h-6" />
          </div>
          <p className="text-slate-500 font-medium">No client found.</p>
        </div>
      )}
      
      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingClient ? "Edit Client" : "Add New Client"}</DialogTitle>
          </DialogHeader>
          <ClientForm
            initialData={editingClient || undefined}
            onSubmit={handleSubmit}
            isSubmitting={isSubmitting}
          />
        </DialogContent>
      </Dialog>

      {/* Activity Log Dialog */}
      <ActivityLogDialog 
        open={logsOpen} 
        onOpenChange={setLogsOpen}
        title="Client Activity Logs"
        subtitle={activeClient?.companyName || "Client"}
        logs={clientLogs}
        isLoading={isLoadingLogs}
      />

      {/* WhatsApp Dialog */}
      <WhatsAppSmmDialog
        open={waDialogOpen}
        onOpenChange={setWaDialogOpen}
        client={waClient}
        onSaved={fetchClients}
      />

      {/* Follow-up Config Dialog */}
      <Dialog open={followupConfigOpen} onOpenChange={setFollowupConfigOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Follow-ups</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="pt-2">
              <Label className="text-sm font-semibold mb-3 block text-slate-700">Past Follow-up Remarks</Label>
              
              <div className="flex gap-2 mb-4">
                <Input 
                  placeholder="Add a new remark..." 
                  value={newRemarkText}
                  onChange={(e) => setNewRemarkText(e.target.value)}
                  className="flex-1"
                />
                <Button 
                  size="icon" 
                  className="bg-brand-teal text-white hover:bg-brand-teal-light shrink-0"
                  onClick={handleAddRemark}
                  disabled={isAddingRemark || !newRemarkText.trim()}
                >
                  {isAddingRemark ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                </Button>
              </div>

              <div className="space-y-3 max-h-[200px] overflow-y-auto pr-2">
                {isLoadingFollowupHistory ? (
                  <div className="flex justify-center py-4"><Loader2 className="w-5 h-5 animate-spin text-brand-teal" /></div>
                ) : followupHistoryLogs.length > 0 ? (
                  followupHistoryLogs.map((log: any, idx: number) => (
                    <div key={idx} className="bg-slate-50 p-3 rounded-lg border border-slate-100 group">
                      <div className="flex justify-between items-start mb-1">
                        <div className="flex flex-col">
                          <span className="text-[10px] font-bold text-slate-500">{new Date(log.timestamp).toLocaleDateString()}</span>
                          <span className="text-[10px] text-slate-400">{log.userName}</span>
                        </div>
                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <Button size="icon" variant="ghost" className="h-6 w-6 text-slate-400 hover:text-brand-teal" onClick={() => {
                            setEditingRemarkId(log.id);
                            setEditingRemarkText(log.details.replace('Remark: ', ''));
                          }}>
                            <Pencil className="w-3 h-3" />
                          </Button>
                          <Button size="icon" variant="ghost" className="h-6 w-6 text-slate-400 hover:text-rose-600" onClick={() => handleDeleteRemark(log.id)}>
                            <Trash2 className="w-3 h-3" />
                          </Button>
                        </div>
                      </div>
                      {editingRemarkId === log.id ? (
                        <div className="flex flex-col gap-2 mt-2">
                          <textarea 
                            value={editingRemarkText} 
                            onChange={e => setEditingRemarkText(e.target.value)} 
                            className="w-full text-xs p-2 border border-brand-teal/50 rounded resize-none focus:outline-none focus:ring-1 focus:ring-brand-teal" 
                            rows={2} 
                            autoFocus 
                          />
                          <div className="flex justify-end gap-1">
                            <Button size="sm" variant="ghost" className="h-6 text-[10px] px-2" onClick={() => setEditingRemarkId(null)}>Cancel</Button>
                            <Button size="sm" className="h-6 text-[10px] px-2 bg-emerald-600 text-white hover:bg-emerald-700" onClick={() => handleUpdateRemark(log.id)}>Save</Button>
                          </div>
                        </div>
                      ) : (
                        <p className="text-xs text-slate-700 whitespace-pre-wrap">{log.details.replace('Remark: ', '')}</p>
                      )}
                    </div>
                  ))
                ) : (
                  <p className="text-center text-xs text-slate-400 py-4 bg-slate-50 rounded border border-slate-100">No past follow-ups found.</p>
                )}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Follow-up Remark Dialog */}
      <Dialog open={followupRemarkOpen} onOpenChange={setFollowupRemarkOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Complete Follow-up</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>Follow-up Remark (Optional)</Label>
              <textarea 
                className="w-full min-h-[100px] border border-slate-200 rounded-md p-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-teal/20 focus:border-brand-teal"
                placeholder="Enter notes about this follow-up..."
                value={followupRemarkText}
                onChange={(e) => setFollowupRemarkText(e.target.value)}
              />
            </div>
            <div className="pt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setFollowupRemarkOpen(false)}>Cancel</Button>
              <Button className="bg-emerald-600 text-white hover:bg-emerald-700" onClick={handleFollowupCompleteWithRemark}>
                <CheckCircle2 className="w-4 h-4 mr-2" /> Mark as Done
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Feedback Config Dialog */}
      <Dialog open={feedbackConfigOpen} onOpenChange={setFeedbackConfigOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Feedback Reminder Schedule</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>Reminder Frequency</Label>
              <Select value={feedbackTypeInput} onValueChange={setFeedbackTypeInput}>
                <SelectTrigger>
                  <SelectValue placeholder="Select type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="Interval">Every X Days</SelectItem>
                  <SelectItem value="Weekly">Weekly</SelectItem>
                  <SelectItem value="Monthly">Monthly</SelectItem>
                </SelectContent>
              </Select>
            </div>
            
            {feedbackTypeInput === "Interval" && (
              <div className="space-y-2">
                <Label>Interval (Days)</Label>
                <Input 
                  type="number" 
                  placeholder="e.g. 15" 
                  value={feedbackIntervalInput} 
                  onChange={(e) => setFeedbackIntervalInput(e.target.value)} 
                />
              </div>
            )}

            {feedbackTypeInput === "Weekly" && (
              <div className="space-y-2">
                <Label>Days of the week</Label>
                <div className="flex flex-wrap gap-2">
                  {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, idx) => {
                    const isSelected = feedbackDaysOfWeekInput.includes(idx);
                    return (
                      <Badge 
                        key={day} 
                        variant={isSelected ? "default" : "outline"}
                        className={`cursor-pointer ${isSelected ? 'bg-brand-teal text-white border-brand-teal hover:bg-brand-teal/90' : 'bg-white hover:bg-slate-50 border-slate-200 text-slate-600'}`}
                        onClick={() => {
                          if (isSelected) setFeedbackDaysOfWeekInput(prev => prev.filter(d => d !== idx));
                          else setFeedbackDaysOfWeekInput(prev => [...prev, idx]);
                        }}
                      >
                        {day}
                      </Badge>
                    );
                  })}
                </div>
              </div>
            )}

            {feedbackTypeInput === "Monthly" && (
              <div className="space-y-2">
                <Label>Dates of the month (1-31)</Label>
                <Input 
                  placeholder="e.g. 1, 15" 
                  value={feedbackDatesOfMonthInput.join(", ")}
                  onChange={(e) => {
                    const parts = e.target.value.split(",").map(s => parseInt(s.trim())).filter(n => !isNaN(n) && n >= 1 && n <= 31);
                    setFeedbackDatesOfMonthInput(parts);
                  }}
                />
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>Last Feedback Collected</Label>
                <Input 
                  type="date" 
                  value={feedbackLastDateInput} 
                  onChange={(e) => setFeedbackLastDateInput(e.target.value)} 
                />
              </div>
              <div className="space-y-2">
                <Label>Next Reminder Date</Label>
                <Input 
                  type="date" 
                  value={feedbackNextDateInput} 
                  onChange={(e) => setFeedbackNextDateInput(e.target.value)} 
                />
              </div>
            </div>

            <div className="pt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setFeedbackConfigOpen(false)}>Cancel</Button>
              <Button className="bg-brand-teal text-white hover:bg-brand-teal/90" onClick={handleSaveFeedbackConfig}>Save Configuration</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Assign Team Dialog */}
      <Dialog open={assignTeamOpen} onOpenChange={setAssignTeamOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Assign Creative Team</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4 max-h-[70vh] overflow-y-auto px-1">
            <div className="space-y-2">
              <Label>Scripting</Label>
              <SearchableEmployeeSelect 
                value={scriptwriterId} 
                onChange={setScriptwriterId} 
                placeholder="Select scriptwriter..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Reel / Editing</Label>
              <SearchableEmployeeSelect 
                value={reelEditorId} 
                onChange={setReelEditorId} 
                placeholder="Select editor..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Post / Graphics</Label>
              <SearchableEmployeeSelect 
                value={postDesignerId} 
                onChange={setPostDesignerId} 
                placeholder="Select designer..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Shoot / Videography</Label>
              <SearchableEmployeeSelect 
                value={shooterId} 
                onChange={setShooterId} 
                placeholder="Select shooter..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Approval / QC</Label>
              <SearchableEmployeeSelect 
                value={approverId} 
                onChange={setApproverId} 
                placeholder="Select approver..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Posting / Publisher</Label>
              <SearchableEmployeeSelect 
                value={posterId} 
                onChange={setPosterId} 
                placeholder="Select poster..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Caption</Label>
              <SearchableEmployeeSelect 
                value={captionWriterId} 
                onChange={setCaptionWriterId} 
                placeholder="Select caption writer..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Thumbnail</Label>
              <SearchableEmployeeSelect 
                value={thumbnailDesignerId} 
                onChange={setThumbnailDesignerId} 
                placeholder="Select designer..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Create WhatsApp Group</Label>
              <SearchableEmployeeSelect 
                value={whatsappGroupCreatorId} 
                onChange={setWhatsappGroupCreatorId} 
                placeholder="Select assignee..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Send Greetings Msg</Label>
              <SearchableEmployeeSelect 
                value={greetingsMsgSenderId} 
                onChange={setGreetingsMsgSenderId} 
                placeholder="Select assignee..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Meetings</Label>
              <SearchableEmployeeSelect 
                value={meetingsAssigneeId} 
                onChange={setMeetingsAssigneeId} 
                placeholder="Select assignee..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Create Content Calendar</Label>
              <SearchableEmployeeSelect 
                value={contentCalendarCreatorId} 
                onChange={setContentCalendarCreatorId} 
                placeholder="Select assignee..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="space-y-2">
              <Label>Client Follow-up</Label>
              <SearchableEmployeeSelect 
                value={followupAssigneeId} 
                onChange={setFollowupAssigneeId} 
                placeholder="Select follow-up assignee..." 
                employees={creativeEmployees} 
              />
            </div>
            <div className="pt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setAssignTeamOpen(false)}>Cancel</Button>
              <Button className="bg-brand-teal text-white hover:bg-brand-teal-light" onClick={handleSaveTeamAssignment}>
                Save Assignments
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      
      <ClientReviewDialog 
        open={reviewDialogOpen} 
        onOpenChange={setReviewDialogOpen} 
        client={reviewClient} 
        onSaved={fetchClients} 
      />
        </TabsContent>
        <TabsContent value="progress" className="m-0 space-y-4">
          <div className="flex items-center">
            <Button 
              variant="ghost" 
              className="gap-2 text-slate-500 hover:text-slate-800 hover:bg-slate-100"
              onClick={() => setActiveTab('projects')}
            >
              <ChevronLeft className="w-4 h-4" /> Back to Projects & Clients
            </Button>
          </div>
          <DailyProgressView defaultDepartment="Creative" />
        </TabsContent>
      </Tabs>
    </div>
  );
}
