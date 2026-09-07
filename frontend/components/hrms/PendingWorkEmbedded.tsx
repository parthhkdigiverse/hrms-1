"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, AlertCircle, CalendarIcon, ArrowRight, Filter, Search, ClipboardList, X, Check, Edit2, History, ArrowLeftRight, ArrowLeft, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { SearchableSelect } from '@/components/ui/searchable-select';
import { API_URL } from '@/lib/config';
import { toast } from 'sonner';
import { useConfirm } from "@/context/ConfirmContext";

const isStageSubsequentOrEqual = (stageName: string, remarkStageName: string, postReel?: string) => {
  const reelStages = ['Script', 'Shoot', 'Brand Person', 'Editing', 'Caption', 'Thumbnail', 'Approval', 'Posting'];
  const postStages = ['Post/Graphics', 'Brand Person', 'Caption', 'Approval', 'Posting'];
  const storyStages = ['Editing', 'Approval'];
  
  const stages = postReel === 'Post' ? postStages : postReel === 'Story' ? storyStages : reelStages;
  const remarkIdx = stages.findIndex(s => s.toLowerCase() === remarkStageName.toLowerCase());
  const stageIdx = stages.findIndex(s => s.toLowerCase() === stageName.toLowerCase());
  
  if (remarkIdx === -1 || stageIdx === -1) return false;
  return stageIdx >= remarkIdx;
};

const parseLocalDate = (dateStr: string) => {
  if (!dateStr) return new Date(0);
  if (dateStr.includes('T')) {
    const d = new Date(dateStr);
    d.setHours(0, 0, 0, 0);
    return d;
  }
  const delimiter = dateStr.includes('-') ? '-' : '/';
  const parts = dateStr.split(delimiter);
  if (parts.length === 3) {
    if (parts[0].length === 4) {
      return new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]), 0, 0, 0, 0);
    } else if (parts[2].length === 4) {
      return new Date(parseInt(parts[2]), parseInt(parts[1]) - 1, parseInt(parts[0]), 0, 0, 0, 0);
    }
  }
  const d = new Date(dateStr);
  d.setHours(0, 0, 0, 0);
  return d;
};

const formatDateToDDMMYYYY = (dateInput: string | Date | undefined): string => {
  if (!dateInput) return '';
  try {
    let dateObj: Date;
    if (dateInput instanceof Date) {
      dateObj = dateInput;
    } else {
      const cleanStr = dateInput.trim();
      if (!cleanStr) return '';
      
      if (cleanStr.includes('T')) {
        dateObj = new Date(cleanStr);
      } else {
        const delimiter = cleanStr.includes('-') ? '-' : '/';
        const parts = cleanStr.split(delimiter);
        if (parts.length === 3) {
          if (parts[0].length === 4) {
            const y = parseInt(parts[0]);
            const m = parseInt(parts[1]);
            const d = parseInt(parts[2]);
            const dd = String(d).padStart(2, '0');
            const mm = String(m).padStart(2, '0');
            return `${dd}-${mm}-${y}`;
          }
          if (parts[2].length === 4) {
            const d = parseInt(parts[0]);
            const m = parseInt(parts[1]);
            const y = parseInt(parts[2]);
            const dd = String(d).padStart(2, '0');
            const mm = String(m).padStart(2, '0');
            return `${dd}-${mm}-${y}`;
          }
        }
        dateObj = new Date(cleanStr);
      }
    }

    if (isNaN(dateObj.getTime())) return '';
    const day = String(dateObj.getDate()).padStart(2, '0');
    const month = String(dateObj.getMonth() + 1).padStart(2, '0');
    const year = dateObj.getFullYear();
    return `${day}-${month}-${year}`;
  } catch (e) {
    return '';
  }
};

export function PendingWorkEmbedded({ 
  type = "pending-work",
  defaultTaskType = "all",
  hideTaskTypeFilter = false,
  hideStageFilter = false,
  hideProjectFilter = false,
  showTransferRequests,
  onShowTransferRequestsChange,
  onRespond
}: { 
  type?: "pending-work" | "todays-work" | "upcoming-work" | "completed-work" | "all",
  defaultTaskType?: string,
  hideTaskTypeFilter?: boolean,
  hideStageFilter?: boolean,
  hideProjectFilter?: boolean,
  showTransferRequests?: boolean,
  onShowTransferRequestsChange?: (val: boolean) => void,
  onRespond?: () => void
}) {
  const router = useRouter();
  const { confirm } = useConfirm();
  
  const [entries, setEntries] = useState<any[]>([]);
  const [otherWorkEntries, setOtherWorkEntries] = useState<any[]>([]);
  const [clients, setClients] = useState<any[]>([]);
  const [clientProjects, setClientProjects] = useState<Record<string, any>>({});
  const [projects, setProjects] = useState<any[]>([]);
  const [employees, setEmployees] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [workScope, setWorkScope] = useState<'my' | 'all'>('my');

  const [filterProject, setFilterProject] = useState<string>('all');
  const [filterStage, setFilterStage] = useState<string>('all');
  const [filterTaskType, setFilterTaskType] = useState<string>(defaultTaskType);
  const [filterAssigner, setFilterAssigner] = useState<string>('all');
  const [filterAssignee, setFilterAssignee] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterDate, setFilterDate] = useState<string>('');

  const [editingRemarkId, setEditingRemarkId] = useState<string | null>(null);
  const [editingRemarkValue, setEditingRemarkValue] = useState<string>('');
  const [editingIsClientIssue, setEditingIsClientIssue] = useState<boolean>(false);
  
  const [incomingRequests, setIncomingRequests] = useState<any[]>([]);
  const [outgoingRequests, setOutgoingRequests] = useState<any[]>([]);

  const filteredIncoming = incomingRequests.filter((r: any) => {
    return r.taskType === 'content-calendar' || r.taskType === 'other-work' || r.taskType === 'creative' || r.taskType === 'dev-creative-work';
  });

  const filteredOutgoing = outgoingRequests.filter((r: any) => {
    return r.taskType === 'content-calendar' || r.taskType === 'other-work' || r.taskType === 'creative' || r.taskType === 'dev-creative-work';
  });
  const [isTransferModalOpen, setIsTransferModalOpen] = useState(false);
  const [viewingTransferRequestsInternal, setViewingTransferRequestsInternal] = useState(false);
  const viewingTransferRequests = showTransferRequests !== undefined ? showTransferRequests : viewingTransferRequestsInternal;
  const setViewingTransferRequests = (val: boolean) => {
    setViewingTransferRequestsInternal(val);
    if (onShowTransferRequestsChange) {
      onShowTransferRequestsChange(val);
    }
  };
  const [requestsTab, setRequestsTab] = useState<'incoming' | 'outgoing'>('incoming');
  const [transferringTask, setTransferringTask] = useState<any>(null);
  const [selectedReceiverId, setSelectedReceiverId] = useState<string>('');
  
  const [logsDialogOpen, setLogsDialogOpen] = useState(false);
  const [currentLogs, setCurrentLogs] = useState<any[]>([]);

  const handleOpenLogs = (entry: any) => {
    setCurrentLogs(entry.logs || []);
    setLogsDialogOpen(true);
  };

  const handleSaveRemark = async (id: string, stage: string, isOtherWork?: boolean) => {
    try {
      const storedUser = localStorage.getItem('user');
      const user = storedUser ? JSON.parse(storedUser) : null;
      const userName = user?.name || (user?.firstName ? `${user.firstName} ${user.lastName || ''}`.trim() : null) || "Unknown User";

      const finalRemark = editingIsClientIssue ? `[CLIENT ISSUE] ${editingRemarkValue}` : editingRemarkValue;

      const endpoint = isOtherWork ? `${API_URL}/other-work/${id}` : `${API_URL}/content-calendar/${id}`;
      const payload = isOtherWork 
        ? { remark: finalRemark, remarkStage: stage, updatedBy: userName } 
        : { remark: finalRemark, remarkStage: stage, updatedBy: userName };

      const response = await fetch(endpoint, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (response.ok) {
        if (isOtherWork) {
          setOtherWorkEntries(otherWorkEntries.map(e => e.id === id ? { ...e, remark: finalRemark, remarkStage: stage } : e));
          setEntries(entries.map(e => e.id === id ? { ...e, remark: finalRemark, remarkStage: stage } : e)); 
        } else {
          setEntries(entries.map(e => e.id === id ? { ...e, remark: finalRemark, remarkStage: stage } : e));
        }
        setEditingRemarkId(null);
        toast.success("Remark saved successfully");
      } else {
        toast.error("Failed to save remark");
      }
    } catch (err) {
      console.error(err);
      toast.error("Failed to save remark");
    }
  };

  const handleUpdateOtherWorkStatus = async (id: string, newStatus: string) => {
    try {
      const storedUser = localStorage.getItem('user');
      const user = storedUser ? JSON.parse(storedUser) : null;
      const userName = user?.name || (user?.firstName ? `${user.firstName} ${user.lastName || ''}`.trim() : null) || "Unknown User";

      const response = await fetch(`${API_URL}/other-work/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus, updatedBy: userName }),
      });
      if (response.ok) {
        setOtherWorkEntries(otherWorkEntries.map(e => e.id === id ? { ...e, status: newStatus } : e));
        toast.success(`Task marked as ${newStatus}`);
      }
    } catch (err) {
      console.error(err);
      toast.error("Failed to update status");
    }
  };

  const handleDeleteOtherWork = async (id: string) => {
    const isConfirmed = await confirm({
      title: "Delete Assigned Work",
      message: "Are you sure you want to delete this task? It will be removed from all assigned user dashboards.",
      confirmText: "Delete",
      destructive: true,
    });

    if (!isConfirmed) return;

    try {
      const response = await fetch(`${API_URL}/other-work/${id}`, {
        method: 'DELETE',
      });
      if (response.ok) {
        setOtherWorkEntries(otherWorkEntries.filter(e => e.id !== id));
        toast.success("Assigned task deleted successfully");
      } else {
        toast.error("Failed to delete assigned task");
      }
    } catch (err) {
      console.error(err);
      toast.error("An error occurred");
    }
  };

  const canTransferTask = (item: any) => {
    const isGlobalAdminOrHR = currentUser?.role?.toLowerCase() === 'admin' || currentUser?.name === 'Admin Admin' || currentUser?.role === 'HR';
    if (isGlobalAdminOrHR) return true;

    const taskDept = defaultTaskType === 'digital-marketing' ? 'Digital Marketing' : 'Creative';
    const userDept = currentUser?.department?.trim();
    if (!userDept) return false;

    const isCreativeUser = userDept.toLowerCase() === 'creative' || userDept.toLowerCase() === 'smm' || userDept.toLowerCase() === 'social media marketing' || userDept.toLowerCase() === 'graphics';
    const isDMUser = userDept.toLowerCase() === 'digital marketing' || userDept.toLowerCase() === 'dm';

    if (taskDept.toLowerCase() === 'creative' && !isCreativeUser) return false;
    if (taskDept.toLowerCase() === 'digital marketing' && !isDMUser) return false;

    const isTeamLeaderOfDept = currentUser?.role === 'Team Leader' || currentUser?.designation?.toLowerCase() === 'team leader';
    if (isTeamLeaderOfDept) return true;

    const isAssignedToUser = item.assigneeId === currentUser?.id || item.assigneeId === currentUser?._id;
    return isAssignedToUser;
  };

  const handleOpenTransferModal = (task: any) => {
    setTransferringTask(task);
    setSelectedReceiverId('');
    setIsTransferModalOpen(true);
  };

  const handleSendTransferRequest = async () => {
    if (!selectedReceiverId) {
      toast.error("Please select an employee to transfer this task to.");
      return;
    }
    const receiver = employees.find((e: any) => e.id === selectedReceiverId);
    if (!receiver) {
      toast.error("Selected employee not found.");
      return;
    }
    try {
      const response = await fetch(`${API_URL}/work-transfer-requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          taskId: transferringTask.id,
          taskType: transferringTask.isOtherWork ? "other-work" : "content-calendar",
          taskName: transferringTask.taskName,
          stage: transferringTask.stage,
          senderId: currentUser.id,
          senderName: currentUser.name || `${currentUser.firstName} ${currentUser.lastName || ''}`.trim(),
          receiverId: receiver.id,
          receiverName: `${receiver.firstName} ${receiver.lastName || ''}`.trim(),
        }),
      });
      if (response.ok) {
        toast.success("Transfer request sent successfully.");
        setIsTransferModalOpen(false);
        fetchData();
      } else {
        const err = await response.json();
        toast.error(err.detail || "Failed to send transfer request.");
      }
    } catch (err) {
      console.error(err);
      toast.error("Failed to send transfer request.");
    }
  };

  const handleRespondRequest = async (requestId: string, status: 'Accepted' | 'Rejected') => {
    try {
      const response = await fetch(`${API_URL}/work-transfer-requests/${requestId}/respond`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      if (response.ok) {
        toast.success(`Transfer request ${status.toLowerCase()} successfully.`);
        fetchData();
        if (onRespond) {
          onRespond();
        }
      } else {
        const err = await response.json();
        toast.error(err.detail || `Failed to ${status.toLowerCase()} request.`);
      }
    } catch (err) {
      console.error(err);
      toast.error(`Failed to ${status.toLowerCase()} request.`);
    }
  };

  const getPendingTransferRequest = (item: any) => {
    return outgoingRequests.find(r => r.taskId === item.id && r.stage === item.stage && r.status === 'Pending');
  };

  useEffect(() => {
    const storedUser = localStorage.getItem('user');
    if (storedUser) {
      try {
        setCurrentUser(JSON.parse(storedUser));
      } catch (err) {
        console.error("Failed to parse user from localStorage", err);
      }
    }
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const storedUser = localStorage.getItem('user');
      const user = storedUser ? JSON.parse(storedUser) : null;
      
      const [entriesRes, clientsRes, pRes, otherWorkRes, employeesRes] = await Promise.all([
        fetch(`${API_URL}/content-calendar/all`),
        fetch(`${API_URL}/clients`),
        fetch(`${API_URL}/projects`),
        fetch(`${API_URL}/other-work/all`),
        fetch(`${API_URL}/employees`)
      ]);

      if (user?.id) {
        const isUserAdminOrTL = (user.designation?.toLowerCase() === 'team leader' || user.designation?.toLowerCase() === 'head') || user.designation?.toLowerCase() === 'hr' || user.role?.toLowerCase() === 'admin' || user.name === 'Admin Admin';
        const taskTypeQuery = `?taskType=smm`;
        if (isUserAdminOrTL) {
          const [allRes, outgoingRes] = await Promise.all([
            fetch(`${API_URL}/work-transfer-requests${taskTypeQuery}`),
            fetch(`${API_URL}/work-transfer-requests/outgoing/${user.id}${taskTypeQuery}`)
          ]);
          if (allRes.ok) {
            setIncomingRequests(await allRes.json());
          }
          if (outgoingRes.ok) {
            setOutgoingRequests(await outgoingRes.json());
          }
        } else {
          const [incomingRes, outgoingRes] = await Promise.all([
            fetch(`${API_URL}/work-transfer-requests/incoming/${user.id}${taskTypeQuery}`),
            fetch(`${API_URL}/work-transfer-requests/outgoing/${user.id}${taskTypeQuery}`)
          ]);
          if (incomingRes.ok) {
            setIncomingRequests(await incomingRes.json());
          }
          if (outgoingRes.ok) {
            setOutgoingRequests(await outgoingRes.json());
          }
        }
      }
      
      if (entriesRes.ok && clientsRes.ok) {
        const fetchedEntries = await entriesRes.json();
        const fetchedClients = await clientsRes.json();
        setEntries(fetchedEntries);
        setClients(fetchedClients);
      }
      
      if (otherWorkRes.ok) {
        const fetchedOtherWork = await otherWorkRes.json();
        setOtherWorkEntries(fetchedOtherWork);
      }
      
      if (employeesRes.ok) {
        const fetchedEmployees = await employeesRes.json();
        setEmployees(fetchedEmployees);
      }
      
      if (pRes.ok) {
        const fetchedProjects = await pRes.json();
        setProjects(fetchedProjects);
        const projectMap: Record<string, any> = {};
        fetchedProjects.forEach((p: any) => {
          if (p.clientId && p.department === 'Creative') {
            projectMap[p.clientId] = p;
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

  const fullRoles = ["admin", "manager", "social media manager", "smm", "director", "head", "super admin", "digital marketer", "digital marketing", "team leader", "sub admin", "sub-admin"];
  const isAdminOrTL = currentUser?.name === 'Admin Admin' || currentUser?.role === 'HR' || fullRoles.includes(currentUser?.role?.toLowerCase() || '') || fullRoles.includes(currentUser?.designation?.toLowerCase() || '');

  const preFilteredTasks = useMemo(() => {
    const tasks: any[] = [];
    const storedUser = localStorage.getItem('user');
    const user = storedUser ? JSON.parse(storedUser) : null;
    const isEmployeeOrIntern = user?.role === "Employee" || user?.role === "Intern";

    entries.forEach(entry => {
      const client = clients.find(c => c.id === entry.clientId);
      let project = null;
      if (entry.projectId) {
        project = projects.find(p => p.id === entry.projectId);
      }
      if (!project) {
        project = clientProjects[entry.clientId];
      }
      if (!project) return; // Only show if active creative project
      if (project.status === "on-hold" || project.status === "onhold" || project.status?.toLowerCase() === "on-hold") return;
      
      const clientName = client ? (client.companyName || client.clientName || 'Unknown Client') : 'Unknown Client';
      const projectName = project.title;
      const displayName = `${projectName} (${clientName})`;

      const enrich = (stage: string, deadline: string, type: string) => {
        let assigneeId = null;
        let assignerId = project.teamLeaderId || client?.teamLeaderId;
        
        if (stage === 'Script') assigneeId = entry.assignedScriptwriterId || project.assignedScriptwriterId || client?.assignedScriptwriterId;
        if (stage === 'Shoot') assigneeId = entry.assignedShooterId || project.assignedShooterId || client?.assignedShooterId;
        if (stage === 'Caption') assigneeId = entry.assignedCaptionWriterId || project.assignedCaptionWriterId || client?.assignedCaptionWriterId;
        if (stage === 'Thumbnail') assigneeId = entry.assignedThumbnailDesignerId || project.assignedThumbnailDesignerId || client?.assignedThumbnailDesignerId;
        if (stage === 'Editing') {
          if (entry.postReel === 'Post') {
            assigneeId = entry.assignedPostDesignerId || project.assignedPostDesignerId || client?.assignedPostDesignerId;
          } else {
            assigneeId = entry.assignedReelEditorId || project.assignedReelEditorId || client?.assignedReelEditorId;
          }
        }
        if (stage === 'Approval') assigneeId = entry.assignedApproverId || project.assignedApproverId || client?.assignedApproverId;
        if (stage === 'Posting') assigneeId = entry.assignedPosterId || project.assignedPosterId || client?.assignedPosterId;

        const assignee = employees.find((e: any) => e.id === assigneeId);
        const assigner = employees.find((e: any) => e.id === assignerId);

        const finalStage = (stage === 'Editing' && entry.postReel === 'Post') ? 'Post/Graphics' : stage;
        const transfer = incomingRequests.find((r: any) => r.taskId === (entry.id || entry._id) && r.stage === finalStage && r.status === 'Accepted' && String(r.receiverId) === String(user?.id));
        return {
          ...entry,
          clientDisplayName: displayName,
          clientId: entry.clientId,
          projectId: entry.projectId || project.id,
          stage: finalStage,
          deadline,
          isTransferredToMe: !!transfer,
          type,
          taskName: entry.concept || entry.topic || (entry.postReel ? `${entry.postReel} Content` : `Task for ${entry.postingDate || entry.monthYear || 'Unknown Date'}`),
          assigneeName: assignee ? `${assignee.firstName} ${assignee.lastName}` : null,
          assignerName: assigner ? `${assigner.firstName} ${assigner.lastName}` : null,
          assigneeId: assigneeId,
          assignerId: assignerId,
        };
      };

      const canSeeTask = (stage: string) => {
        const uId = user?.id;
        if (!uId) return false;
        
        const finalStageName = (stage === 'Editing' && entry.postReel === 'Post') ? 'Post/Graphics' : stage;
        
        // Hide task if the current user has sent a transfer request that has been accepted
        const outgoingTransfer = outgoingRequests.find(r => r.taskId === (entry.id || entry._id) && r.stage === finalStageName && r.status === 'Accepted');
        if (outgoingTransfer && workScope === 'my') return false;

        const transfer = incomingRequests.find(r => r.taskId === (entry.id || entry._id) && r.stage === finalStageName && r.status === 'Accepted');
        
        let isAssignedToMe = false;
        if (stage === 'Script') isAssignedToMe = String(transfer ? transfer.receiverId : (entry.assignedScriptwriterId || project.assignedScriptwriterId || client?.assignedScriptwriterId)) === String(uId);
        else if (stage === 'Shoot') isAssignedToMe = String(transfer ? transfer.receiverId : (entry.assignedShooterId || project.assignedShooterId || client?.assignedShooterId)) === String(uId);
        else if (stage === 'Caption') isAssignedToMe = String(transfer ? transfer.receiverId : (entry.assignedCaptionWriterId || project.assignedCaptionWriterId || client?.assignedCaptionWriterId)) === String(uId);
        else if (stage === 'Thumbnail') isAssignedToMe = String(transfer ? transfer.receiverId : (entry.assignedThumbnailDesignerId || project.assignedThumbnailDesignerId || client?.assignedThumbnailDesignerId)) === String(uId);
        else if (stage === 'Editing') {
          if (entry.postReel === 'Post') {
            isAssignedToMe = String(transfer ? transfer.receiverId : (entry.assignedPostDesignerId || project.assignedPostDesignerId || client?.assignedPostDesignerId)) === String(uId);
          } else {
            isAssignedToMe = String(transfer ? transfer.receiverId : (entry.assignedReelEditorId || project.assignedReelEditorId || client?.assignedReelEditorId)) === String(uId);
          }
        }
        else if (stage === 'Approval') isAssignedToMe = String(transfer ? transfer.receiverId : (entry.assignedApproverId || project.assignedApproverId || client?.assignedApproverId)) === String(uId);
        else if (stage === 'Posting') isAssignedToMe = String(transfer ? transfer.receiverId : (entry.assignedPosterId || project.assignedPosterId || client?.assignedPosterId)) === String(uId);
        
        if (isAdminOrTL && (type === 'all' || workScope === 'all')) return true;
        return isAssignedToMe;
      };

      const isPost = entry.postReel === 'Post';
      const isStory = entry.postReel === 'Story';

      if (!isPost && !isStory && entry.scriptDate && entry.scriptDate !== '-' && !entry.scriptLink && canSeeTask('Script')) tasks.push(enrich('Script', entry.scriptDate, 'scripts'));
      if (!isPost && !isStory && entry.shootDate && entry.shootDate !== '-' && !entry.shootLink && entry.shootLink !== '-' && canSeeTask('Shoot')) tasks.push(enrich('Shoot', entry.shootDate, 'shoots'));
      
      if (!isStory && entry.assignedBrandPersonIds && (!entry.shootLink || entry.shootLink === '-')) {
        const bpIdsRaw = entry.assignedBrandPersonIds;
        const bpIds = Array.isArray(bpIdsRaw) ? bpIdsRaw : (typeof bpIdsRaw === 'string' ? bpIdsRaw.split(',').map((id: string) => id.trim()).filter(Boolean) : []);
        bpIds.forEach((bpId: string) => {
          const isAssignedToMe = user?.id === bpId;
          if ((isAdminOrTL && (type === 'all' || workScope === 'all')) || isAssignedToMe) {
             const assignerId = project.teamLeaderId || client?.teamLeaderId;
             const assigner = employees.find((e: any) => e.id === assignerId);
             const assignee = employees.find((e: any) => e.id === bpId);
             
             const taskDeadline = entry.shootDate || entry.postingDate || (entry.monthYear ? `${entry.monthYear}-28` : new Date().toISOString().split('T')[0]);
             
             tasks.push({
               ...entry,
               clientDisplayName: displayName,
               clientId: entry.clientId,
               projectId: entry.projectId || project.id,
               stage: 'Brand Person',
               deadline: taskDeadline,
               type: 'brand-person',
               taskName: entry.concept || entry.topic || (entry.postReel ? `${entry.postReel} Content` : `Task for ${entry.postingDate || entry.monthYear || 'Unknown Date'}`),
               assigneeName: assignee ? (assignee.name || `${assignee.firstName} ${assignee.lastName}`) : null,
               assignerName: assigner ? (assigner.name || `${assigner.firstName} ${assigner.lastName}`) : null,
               assigneeId: bpId,
               assignerId: assignerId,
             });
          }
        });
      }

      const captionDate = entry.captionDate || entry.editingStart;
      if (!isStory && captionDate && captionDate !== '-' && !entry.caption && entry.caption !== '-' && canSeeTask('Caption')) tasks.push(enrich('Caption', captionDate, 'captions'));

      const thumbnailDate = entry.thumbnailDate || entry.editingStart;
      if (!isPost && !isStory && thumbnailDate && thumbnailDate !== '-' && !entry.thumbnailLink && entry.thumbnailLink !== '-' && canSeeTask('Thumbnail')) tasks.push(enrich('Thumbnail', thumbnailDate, 'thumbnails'));

      const isEditingPending = entry.editingStart && entry.editingStart !== '-' && (isPost ? !entry.finalPostLink : !entry.finalReelLink);
      if (isEditingPending && canSeeTask('Editing')) tasks.push(enrich('Editing', entry.editingStart, 'edits'));
      if (entry.approval && entry.approval !== '-' && entry.isApproved !== 'Yes' && canSeeTask('Approval')) tasks.push(enrich('Approval', entry.approval, 'approvals'));
      if (!isStory && entry.postingDate && entry.postingDate !== '-' && !entry.postingLinkOfIg && entry.postingLinkOfIg !== '-' && canSeeTask('Posting')) tasks.push(enrich('Posting', entry.postingDate, 'posts'));
    });

    if (projects) {
      projects.forEach(project => {
        if (project.status === "on-hold" || project.status === "onhold" || project.status?.toLowerCase() === "on-hold") return;
        if (!project.nextFollowupDate) return;
        const nextDate = project.nextFollowupDate.split("T")[0].split(" ")[0];
        const client = clients.find(c => c.id === project.clientId) || {};
        
        const followUpAssigneeId = project.assignedFollowUpId || client.assignedFollowUpId;
        
        const isEmployeeOrIntern = ['intern', 'employee'].includes(user?.role?.toLowerCase() || "");
        if (isEmployeeOrIntern && user?.id !== followUpAssigneeId) return;

        const assignerId = project.teamLeaderId || client.teamLeaderId;
        const assignee = employees.find((e: any) => e.id === followUpAssigneeId);
        const assigner = employees.find((e: any) => e.id === assignerId);

        tasks.push({
          id: `${project.id}-Followup`,
          clientDisplayName: client.companyName || client.name || project.title || "Client",
          clientId: project.clientId || project.id,
          stage: 'Follow-up',
          deadline: nextDate,
          type: 'followup',
          taskName: `Follow-up for ${project.title || client.companyName || 'Project'}`,
          assigneeName: assignee ? (assignee.name || `${assignee.firstName} ${assignee.lastName}`) : null,
          assignerName: assigner ? (assigner.name || `${assigner.firstName} ${assigner.lastName}`) : null,
          assigneeId: followUpAssigneeId,
          assignerId: assignerId,
          projectId: project.id
        });
      });
    }

    otherWorkEntries.forEach(ow => {
      const uId = user?.id || user?._id;
      const transfer = incomingRequests.find(r => r.taskId === (ow.id || ow._id) && (r.taskType === 'other-work' || r.taskType === 'dm-other-work' || r.taskType === 'creative') && r.status === 'Accepted');
      const currentAssigneeId = transfer ? transfer.receiverId : ow.assigneeId;
      const isAssignee = String(currentAssigneeId) === String(uId) || (user?.name && ow.assigneeName && ow.assigneeName.toLowerCase().includes(user.name.toLowerCase()));
      const isAssigner = String(ow.assignerId) === String(uId) || (user?.name && ow.assignerName && ow.assignerName.toLowerCase().includes(user.name.toLowerCase()));
      
      // Assignee sees it in their today/upcoming/pending until they submit for review
      // Assigner sees it in their pending when it's Ready for Review
      let canSee = false;
      if (ow.status === 'Pending') canSee = isAssignee || isAssigner;
      else if (ow.status === 'Ready for Review') canSee = isAssigner || isAssignee;
      else if (ow.status === 'Approved') canSee = isAssignee || isAssigner;

      const userDeptName = (user?.department || "").toLowerCase();
      const userDesigName = (user?.designation || "").toLowerCase();
      const userRoleName = (user?.role || "").toLowerCase();
      const isManagerOrAdmin = ['Team Leader', 'Admin', 'HR', 'Manager', 'Social Media Manager'].includes(user?.role) || userRoleName === 'admin' || userDesigName === 'team leader' || userDesigName === 'head' || userRoleName === 'team leader' || userRoleName === 'head' || userDeptName.includes('marketing') || userDeptName.includes('dm');
      if ((isManagerOrAdmin && (type === 'all' || workScope === 'all')) || isAssignee || isAssigner) {
        if (type === 'all' || (type === 'completed-work' ? ow.status === 'Approved' : ow.status !== 'Approved')) {
          
          const assignee = employees.find((e: any) => String(e.id) === String(ow.assigneeId));
          const assigner = employees.find((e: any) => String(e.id) === String(ow.assignerId));

          tasks.push({
            ...ow,
            clientDisplayName: ow.taskType === 'digital-marketing' ? 'Digital Marketing' : 'Other Work',
            clientId: 'other-work',
            stage: ow.status,
            deadline: ow.deadline,
            type: ow.taskType || 'other-work',
            taskName: ow.title,
            assigneeName: assignee ? `${assignee.firstName} ${assignee.lastName}` : (ow.assigneeName || null),
            assignerName: assigner ? `${assigner.firstName} ${assigner.lastName}` : (ow.assignerName || null),
            isOtherWork: true,
            isTransferredToMe: !!transfer && String(transfer.receiverId) === String(user?.id)
          });
        }
      }
    });

    // Add client project follow-ups
    Object.values(clientProjects).forEach((project: any) => {
      if (project.status === "on-hold" || project.status === "onhold" || project.status?.toLowerCase() === "on-hold") return;
      if (!project.nextFollowupDate) return;
      
      const client = clients.find(c => c.id === project.clientId);
      const clientName = client ? (client.companyName || client.clientName || 'Unknown Client') : 'Unknown Client';
      const displayName = `${project.title} (${clientName})`;
      
      const assigneeId = project.teamLeaderId || project.assignedEmployeeId;
      const uId = user?.id;
      const isAssignee = assigneeId === uId;
      
      const isManagerOrAdmin = ['Team Leader', 'Admin', 'HR', 'Manager', 'Social Media Manager'].includes(user?.role) || user?.role?.toLowerCase() === 'admin';
      
      if ((isManagerOrAdmin && (type === 'all' || workScope === 'all')) || isAssignee) {
        const deadlineStr = typeof project.nextFollowupDate === 'string' && project.nextFollowupDate.includes('T') 
          ? project.nextFollowupDate.split('T')[0] 
          : String(project.nextFollowupDate);
        
        tasks.push({
          id: `${project.id}-followup`,
          clientDisplayName: displayName,
          clientId: project.clientId,
          projectId: project.id,
          stage: 'Follow-up',
          deadline: deadlineStr,
          type: 'follow-up',
          taskName: 'Client Follow-up',
          assigneeName: project.teamLeaderName || project.assignedEmployeeName || 'Unassigned',
          assignerName: null,
          assigneeId: assigneeId,
          assignerId: null,
          isFollowup: true
        });
      }
    });

    // Add assigned SMM onboarding tasks
    Object.values(clientProjects).forEach((project: any) => {
      if (project.status === "on-hold" || project.status === "onhold" || project.status?.toLowerCase() === "on-hold") return;
      
      const client = clients.find(c => c.id === project.clientId) || {};
      const clientName = client ? (client.companyName || client.clientName || 'Unknown Client') : 'Unknown Client';
      const displayName = `${project.title} (${clientName})`;
      
      const uId = user?.id;
      const isManagerOrAdmin = ['Team Leader', 'Admin', 'HR', 'Manager', 'Social Media Manager'].includes(user?.role) || user?.role?.toLowerCase() === 'admin';
      const deadlineStr = new Date().toISOString().split('T')[0];

      // 1. WhatsApp Group
      const waAssigneeId = project.assignedWhatsappGroupCreatorId || client.assignedWhatsappGroupCreatorId;
      if (waAssigneeId && !client.whatsappGroup) {
        if ((isManagerOrAdmin && (type === 'all' || workScope === 'all')) || waAssigneeId === uId) {
          const assignee = employees.find((e: any) => e.id === waAssigneeId);
          tasks.push({
            id: `${project.id}-whatsapp`,
            clientDisplayName: displayName,
            clientId: project.clientId || project.id,
            projectId: project.id,
            stage: 'WhatsApp Group',
            deadline: deadlineStr,
            type: 'whatsapp-group',
            taskName: 'Create WhatsApp Group',
            assigneeName: assignee ? `${assignee.firstName} ${assignee.lastName}` : 'Unassigned',
            assigneeId: waAssigneeId,
            assignerName: null,
            assignerId: null,
            isFollowup: false
          });
        }
      }

      // 2. Greetings Msg
      const greetingAssigneeId = project.assignedGreetingsMsgSenderId || client.assignedGreetingsMsgSenderId;
      if (greetingAssigneeId && !client.greetingsMsgSent) {
        if ((isManagerOrAdmin && (type === 'all' || workScope === 'all')) || greetingAssigneeId === uId) {
          const assignee = employees.find((e: any) => e.id === greetingAssigneeId);
          tasks.push({
            id: `${project.id}-greetings`,
            clientDisplayName: displayName,
            clientId: project.clientId || project.id,
            projectId: project.id,
            stage: 'Greetings Msg',
            deadline: deadlineStr,
            type: 'greetings-msg',
            taskName: 'Send Greetings Msg',
            assigneeName: assignee ? `${assignee.firstName} ${assignee.lastName}` : 'Unassigned',
            assigneeId: greetingAssigneeId,
            assignerName: null,
            assignerId: null,
            isFollowup: false
          });
        }
      }

      // 3. Meetings
      const meetingAssigneeId = project.assignedMeetingsAssigneeId || client.assignedMeetingsAssigneeId;
      if (meetingAssigneeId && (!client.meetings || client.meetings.length === 0)) {
        if ((isManagerOrAdmin && (type === 'all' || workScope === 'all')) || meetingAssigneeId === uId) {
          const assignee = employees.find((e: any) => e.id === meetingAssigneeId);
          tasks.push({
            id: `${project.id}-meetings`,
            clientDisplayName: displayName,
            clientId: project.clientId || project.id,
            projectId: project.id,
            stage: 'Meeting',
            deadline: deadlineStr,
            type: 'meetings',
            taskName: 'Schedule/Conduct Meeting',
            assigneeName: assignee ? `${assignee.firstName} ${assignee.lastName}` : 'Unassigned',
            assigneeId: meetingAssigneeId,
            assignerName: null,
            assignerId: null,
            isFollowup: false
          });
        }
      }

      // 4. Content Calendar Create
      const ccAssigneeId = project.assignedContentCalendarCreatorId || client.assignedContentCalendarCreatorId;
      const ccCreated = entries.some(e => e.clientId === project.clientId || e.projectId === project.id);
      if (ccAssigneeId && !ccCreated) {
        if ((isManagerOrAdmin && (type === 'all' || workScope === 'all')) || ccAssigneeId === uId) {
          const assignee = employees.find((e: any) => e.id === ccAssigneeId);
          tasks.push({
            id: `${project.id}-cc-create`,
            clientDisplayName: displayName,
            clientId: project.clientId || project.id,
            projectId: project.id,
            stage: 'Content Calendar',
            deadline: deadlineStr,
            type: 'content-calendar-create',
            taskName: 'Create Content Calendar (Sheet)',
            assigneeName: assignee ? `${assignee.firstName} ${assignee.lastName}` : 'Unassigned',
            assigneeId: ccAssigneeId,
            assignerName: null,
            assignerId: null,
            isFollowup: false
          });
        }
      }
    });

    // Apply Scope Filter (My Work vs All Work)
    let filteredTasks = tasks;
    if (workScope === 'my') {
      const uId = user?.id || user?._id;
      filteredTasks = filteredTasks.filter(t => String(t.assigneeId) === String(uId) || String(t.assignerId) === String(uId) || (user?.name && t.assigneeName && t.assigneeName.toLowerCase().includes(user.name.toLowerCase())));
    }

    // Apply Project Filter
    if (filterProject !== 'all') {
      filteredTasks = filteredTasks.filter(t => t.projectId === filterProject || t.clientId === filterProject);
    }

    // Apply Task Type Filter
    if (filterTaskType !== 'all') {
      if (filterTaskType === 'other-work') {
        filteredTasks = filteredTasks.filter(t => t.isOtherWork && t.type !== 'digital-marketing' && t.type !== 'dm-other-work');
      } else if (filterTaskType === 'content-calendar') {
        filteredTasks = filteredTasks.filter(t => !t.isOtherWork && t.type !== 'digital-marketing' && t.type !== 'dm-other-work' && !t.isFollowup);
      } else if (filterTaskType === 'smm-all') {
        filteredTasks = filteredTasks.filter(t => {
          if (t.type === 'digital-marketing' || t.type === 'dm-other-work' || t.taskType === 'dm-other-work' || t.taskType === 'digital-marketing') return false;
          const assigneeEmp = employees.find((e: any) => String(e.id) === String(t.assigneeId));
          if (assigneeEmp?.department?.toLowerCase().includes('marketing') || assigneeEmp?.department?.toLowerCase().includes('dm')) return false;
          return true;
        });
      } else if (filterTaskType === 'digital-marketing') {
        filteredTasks = filteredTasks.filter(t => t.type === 'digital-marketing');
      } else if (filterTaskType === 'dm-other-work') {
        filteredTasks = filteredTasks.filter(t => {
          if (!t.isOtherWork) return false;
          if (t.type === 'dm-other-work' || t.taskType === 'dm-other-work' || t.type === 'digital-marketing' || t.taskType === 'digital-marketing') {
            return true;
          }
          const assigneeEmp = employees.find((e: any) => String(e.id) === String(t.assigneeId));
          const assignerEmp = employees.find((e: any) => String(e.id) === String(t.assignerId));
          const isAssigneeDM = assigneeEmp?.department?.toLowerCase().includes('marketing') || assigneeEmp?.department?.toLowerCase().includes('dm');
          const isAssignerDM = assignerEmp?.department?.toLowerCase().includes('marketing') || assignerEmp?.department?.toLowerCase().includes('dm');
          const isCurrentDM = (user?.department || "").toLowerCase().includes('marketing') || (user?.department || "").toLowerCase().includes('dm');
          return isAssigneeDM || isAssignerDM || isCurrentDM;
        });
      } else if (filterTaskType === 'dev-creative-work') {
        filteredTasks = filteredTasks.filter(t => t.type === 'dev-creative-work');
      } else if (filterTaskType === 'follow-up') {
        filteredTasks = filteredTasks.filter(t => t.isFollowup);
      }
    }



    // Apply Assigner Filter
    const assignerFilterName = filterAssigner !== 'all' ? (filterAssigner.includes('|') ? filterAssigner.split('|')[0] : filterAssigner) : 'all';
    if (assignerFilterName !== 'all') {
      filteredTasks = filteredTasks.filter(t => t.assignerName && t.assignerName === assignerFilterName);
    }
    
    const assigneeFilterName = filterAssignee !== 'all' ? (filterAssignee.includes('|') ? filterAssignee.split('|')[0] : filterAssignee) : 'all';
    if (assigneeFilterName !== 'all') {
      filteredTasks = filteredTasks.filter(t => t.assigneeName && t.assigneeName === assigneeFilterName);
    }

    // Apply Search Query
    if (searchQuery.trim()) {
      const lowerQ = searchQuery.toLowerCase();
      filteredTasks = filteredTasks.filter(t => 
        t.clientDisplayName.toLowerCase().includes(lowerQ) || 
        t.taskName.toLowerCase().includes(lowerQ) ||
        t.stage.toLowerCase().includes(lowerQ)
      );
    }

    // Apply Date Filter
    if (filterDate) {
      filteredTasks = filteredTasks.filter(t => {
        if (t.isOtherWork) {
           const assignDateStr = t.created_at ? t.created_at.split('T')[0] : t.deadline;
           return assignDateStr === filterDate || t.deadline === filterDate;
        }
        return t.deadline === filterDate;
      });
    }

    if (type && type !== 'all') {
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      filteredTasks = filteredTasks.filter(t => {
        if (t.isOtherWork) {
          const isClientIssue = (t.status && t.status.toLowerCase() === 'client issue') || 
                                (t.remark && typeof t.remark === 'string' && t.remark.includes('[CLIENT ISSUE]'));

          if (type === 'completed-work') return t.status === 'Approved';
          if (type === 'pending-work') return t.status === 'Pending' || t.status === 'Ready for Review' || isClientIssue;
          
          if (isClientIssue) return false;

          const deadlineDate = parseLocalDate(t.deadline);
          
          if (type === 'todays-work') return deadlineDate <= today && t.status !== 'Approved';
          if (type === 'upcoming-work') return deadlineDate > today && t.status !== 'Approved';
          return true;
        }

        const hasApplicableRemark = t.remark && typeof t.remark === 'string' && t.remark.trim() !== '' && (
          !t.remarkStage || 
          isStageSubsequentOrEqual(t.stage, t.remarkStage, t.postReel)
        );
        const isClientIssue = (hasApplicableRemark && t.remark.includes('[CLIENT ISSUE]')) || 
                              (t.status && t.status.toLowerCase() === 'client issue');

        if (type === 'pending-work') {
          return isClientIssue;
        }
        
        if (type === 'completed-work') {
          return false; // For now, only Other Work is shown in completed work
        }

        // If it's a client issue, it should ONLY show in pending work (and all)!
        if (isClientIssue) return false;
        
        const deadlineDate = parseLocalDate(t.deadline);
        
        if (type === 'todays-work') return deadlineDate <= today;
        if (type === 'upcoming-work') return deadlineDate > today;
        return true;
      });
    }

    filteredTasks.sort((a, b) => new Date(a.deadline).getTime() - new Date(b.deadline).getTime());
    return filteredTasks;
  }, [entries, otherWorkEntries, clients, clientProjects, projects, filterProject, filterTaskType, filterAssigner, filterAssignee, searchQuery, filterDate, type, employees, workScope]);

  const allPendingTasks = useMemo(() => {
    if (filterStage === 'all') return preFilteredTasks;
    return preFilteredTasks.filter(t => t.stage.toLowerCase() === filterStage.toLowerCase());
  }, [preFilteredTasks, filterStage]);

  const availableStages = useMemo(() => {
    const defaultStages = ['script', 'shoot', 'brand person', 'caption', 'thumbnail', 'editing', 'post/graphics', 'approval', 'posting', 'follow-up', 'whatsapp group', 'greetings msg', 'meeting', 'content calendar'];
    if (isAdminOrTL) {
      return defaultStages;
    }
    const stages = new Set<string>();
    preFilteredTasks.forEach(t => {
      if (t.stage) {
        stages.add(t.stage.toLowerCase());
      }
    });
    return defaultStages.filter(s => stages.has(s));
  }, [preFilteredTasks, isAdminOrTL]);

  const getTaskDueDate = (req: any) => {
    if (req.taskType === 'content-calendar') {
      const entry = entries.find(e => e.id === req.taskId || e._id === req.taskId);
      if (!entry) return '';
      let dueDate = '';
      const stage = req.stage;
      if (stage === 'Script') dueDate = entry.scriptDate;
      else if (stage === 'Shoot') dueDate = entry.shootDate;
      else if (stage === 'Caption') dueDate = entry.captionDate || entry.editingStart;
      else if (stage === 'Thumbnail') dueDate = entry.thumbnailDate || entry.editingStart;
      else if (stage === 'Editing' || stage === 'Post/Graphics') dueDate = entry.editingStart;
      else if (stage === 'Approval') dueDate = entry.approval;
      else if (stage === 'Posting') dueDate = entry.postingDate;
      else if (stage === 'Brand Person') dueDate = entry.shootDate || entry.postingDate;

      if (!dueDate) {
        dueDate = entry.postingDate || entry.scriptDate || entry.shootDate || entry.editingStart || entry.approval || '';
      }

      if (dueDate && dueDate.includes('T')) {
        dueDate = dueDate.split('T')[0];
      }
      return dueDate;
    } else {
      const ow = otherWorkEntries.find(e => e.id === req.taskId || e._id === req.taskId);
      if (!ow) return '';
      let dueDate = ow.deadline || '';
      if (dueDate && dueDate.includes('T')) {
        dueDate = dueDate.split('T')[0];
      }
      return dueDate;
    }
  };

  const renderTaskDetails = (req: any) => {
    if (req.taskType === 'content-calendar') {
      const entry = entries.find(e => e.id === req.taskId || e._id === req.taskId);
      if (!entry) return null;
      const client = clients.find(c => c.id === entry.clientId);
      const cName = client ? (client.companyName || client.clientName) : '';
      return (
        <div className="flex flex-col gap-0.5 mt-1.5 p-2 bg-slate-50 rounded border border-slate-100 text-[10px] font-normal text-slate-500 max-w-sm">
          {cName && <div><span className="font-semibold text-slate-600">Client:</span> {cName}</div>}
          {entry.postReel && <div><span className="font-semibold text-slate-600">Format:</span> {entry.postReel}</div>}
        </div>
      );
    } else {
      const ow = otherWorkEntries.find(e => e.id === req.taskId || e._id === req.taskId);
      if (!ow) return null;
      return (
        <div className="flex flex-col gap-0.5 mt-1.5 p-2 bg-slate-50 rounded border border-slate-100 text-[10px] font-normal text-slate-500 max-w-sm">
          {ow.clientDisplayName && <div><span className="font-semibold text-slate-600">Client:</span> {ow.clientDisplayName}</div>}
        </div>
      );
    }
  };

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden min-h-[calc(100vh-250px)] flex flex-col">
      {viewingTransferRequests ? (
        <div className="flex flex-col flex-1">
          {/* Header Row */}
          <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setViewingTransferRequests(false)}
                className="h-8 px-2.5 hover:bg-slate-200/60 text-slate-600 rounded-lg flex items-center gap-1.5 font-bold text-xs"
              >
                <ArrowLeft className="w-4 h-4" />
                Back to Tasks
              </Button>
              <div className="h-4 w-px bg-slate-300" />
              <div className="flex items-center gap-2">
                <ArrowLeftRight className="w-5 h-5 text-brand-teal" />
                <h2 className="text-sm font-bold text-slate-800 uppercase tracking-wider">
                  Task Transfer Requests
                </h2>
              </div>
            </div>
          </div>

          {/* Custom Tabs Headers */}
          <div className="flex border-b border-slate-200 px-6 bg-slate-50/50">
            <button
              onClick={() => setRequestsTab('incoming')}
              className={`py-3 px-4 text-xs font-bold border-b-2 -mb-px transition-all ${
                requestsTab === 'incoming'
                  ? 'border-brand-teal text-brand-teal'
                  : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {isAdminOrTL ? 'All Requests' : 'Received Requests'} ({filteredIncoming.length})
            </button>
            <button
              onClick={() => setRequestsTab('outgoing')}
              className={`py-3 px-4 text-xs font-bold border-b-2 -mb-px transition-all ${
                requestsTab === 'outgoing'
                  ? 'border-brand-teal text-brand-teal'
                  : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {isAdminOrTL ? 'My Sent Requests' : 'Sent Requests'} ({filteredOutgoing.length})
            </button>
          </div>

          <div className="flex-1 overflow-x-auto bg-white">
            {requestsTab === 'incoming' ? (
              filteredIncoming.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-2">
                  <ArrowLeftRight className="w-8 h-8 text-slate-300 animate-pulse" />
                  <p className="text-sm font-medium">No transfer requests.</p>
                </div>
              ) : (
                <table className="w-full text-left text-sm text-slate-600">
                  <thead className="bg-slate-50/80 border-b border-slate-200 text-slate-500 text-xs uppercase tracking-wider font-semibold">
                    <tr>
                      <th className="px-6 py-4 whitespace-nowrap">Transfer Date</th>
                      <th className="px-6 py-4 whitespace-nowrap">Due Date</th>
                      <th className="px-6 py-4 whitespace-nowrap">Task Name</th>
                      <th className="px-6 py-4 whitespace-nowrap">Stage</th>
                      <th className="px-6 py-4 whitespace-nowrap">From</th>
                      {isAdminOrTL && <th className="px-6 py-4 whitespace-nowrap">To</th>}
                      <th className="px-6 py-4 whitespace-nowrap">Status</th>
                      <th className="px-6 py-4 text-right whitespace-nowrap">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {filteredIncoming.map(req => (
                      <tr key={req.id} className="hover:bg-slate-50/50 transition-colors">
                        <td className="px-6 py-4 whitespace-nowrap text-xs text-slate-500">
                          {formatDateToDDMMYYYY(req.createdDate) || '-'}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-xs text-slate-500 font-semibold">
                          {formatDateToDDMMYYYY(getTaskDueDate(req)) || '-'}
                        </td>
                        <td className="px-6 py-4 font-semibold text-slate-800">
                          {req.taskName}
                          {renderTaskDetails(req)}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <Badge variant="outline" className="text-xs text-brand-teal border-brand-teal/30 bg-brand-teal/5">
                            {req.stage}
                          </Badge>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap font-medium text-slate-700">
                          {req.senderName}
                        </td>
                        {isAdminOrTL && (
                          <td className="px-6 py-4 whitespace-nowrap font-medium text-slate-700">
                            {req.receiverName}
                          </td>
                        )}
                        <td className="px-6 py-4 whitespace-nowrap">
                          <Badge 
                            variant="secondary"
                            className={`text-[10px] font-bold uppercase px-2.5 py-0.5 rounded-full ${
                              req.status === 'Pending' 
                                ? 'bg-amber-100 text-amber-700' 
                                : req.status === 'Accepted'
                                ? 'bg-emerald-100 text-emerald-700'
                                : 'bg-rose-100 text-rose-700'
                            }`}
                          >
                            {req.status}
                          </Badge>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-right">
                          {req.status === 'Pending' ? (
                            <div className="flex gap-2 justify-end">
                              <Button 
                                size="sm" 
                                variant="outline" 
                                className="text-xs h-7 px-3 text-slate-600 border-slate-200 hover:bg-slate-100 rounded-lg font-medium"
                                onClick={() => handleRespondRequest(req.id, 'Rejected')}
                              >
                                Reject
                              </Button>
                              <Button 
                                size="sm" 
                                className="text-xs h-7 px-3 bg-brand-teal hover:bg-brand-teal/90 text-white rounded-lg font-semibold shadow-xs"
                                onClick={() => handleRespondRequest(req.id, 'Accepted')}
                              >
                                Accept
                              </Button>
                            </div>
                          ) : (
                            <span className="text-xs text-slate-400 font-medium italic">Processed</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            ) : (
              filteredOutgoing.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-2">
                  <ArrowLeftRight className="w-8 h-8 text-slate-300 animate-pulse" />
                  <p className="text-sm font-medium">No sent transfer requests.</p>
                </div>
              ) : (
                <table className="w-full text-left text-sm text-slate-600">
                  <thead className="bg-slate-50/80 border-b border-slate-200 text-slate-500 text-xs uppercase tracking-wider font-semibold">
                    <tr>
                      <th className="px-6 py-4 whitespace-nowrap">Transfer Date</th>
                      <th className="px-6 py-4 whitespace-nowrap">Due Date</th>
                      <th className="px-6 py-4 whitespace-nowrap">Task Name</th>
                      <th className="px-6 py-4 whitespace-nowrap">Stage</th>
                      <th className="px-6 py-4 whitespace-nowrap">To</th>
                      <th className="px-6 py-4 whitespace-nowrap">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {filteredOutgoing.map(req => (
                      <tr key={req.id} className="hover:bg-slate-50/50 transition-colors">
                        <td className="px-6 py-4 whitespace-nowrap text-xs text-slate-500">
                          {formatDateToDDMMYYYY(req.createdDate) || '-'}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-xs text-slate-500 font-semibold">
                          {formatDateToDDMMYYYY(getTaskDueDate(req)) || '-'}
                        </td>
                        <td className="px-6 py-4 font-semibold text-slate-800">
                          {req.taskName}
                          {renderTaskDetails(req)}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <Badge variant="outline" className="text-xs text-brand-teal border-brand-teal/30 bg-brand-teal/5">
                            {req.stage}
                          </Badge>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap font-medium text-slate-700">
                          {req.receiverName}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <Badge 
                            variant="secondary"
                            className={`text-[10px] font-bold uppercase px-2.5 py-0.5 rounded-full ${
                              req.status === 'Pending' 
                                ? 'bg-amber-100 text-amber-700' 
                                : req.status === 'Accepted'
                                ? 'bg-emerald-100 text-emerald-700'
                                : 'bg-rose-100 text-rose-700'
                            }`}
                          >
                            {req.status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            )}
          </div>
        </div>
      ) : (
        <>
          {/* Filters Bar */}
          <div className="flex flex-col border-b border-slate-200 bg-slate-50/50">
            {/* Top Header Row */}
            <div className="p-4 pb-3 flex flex-col sm:flex-row gap-4 items-center justify-between">
              <div className="flex items-center justify-between sm:justify-start gap-4 w-full sm:w-auto">
                <div className="flex items-center gap-2">
                  <ClipboardList className="w-5 h-5 text-brand-teal" />
                  <h2 className="text-sm font-bold text-slate-800 uppercase tracking-wider">
                    {type === 'todays-work' ? "Today's Work" : type === 'upcoming-work' ? 'Upcoming Work' : type === 'completed-work' ? 'Completed Work' : type === 'all' ? 'All Tasks' : 'Pending Work'}
                  </h2>
                </div>

                {defaultTaskType !== 'digital-marketing' && defaultTaskType !== 'dev-creative-work' && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setViewingTransferRequests(true)}
                    className="bg-white border-slate-200 hover:bg-slate-50 text-slate-700 rounded-full flex items-center gap-1.5 font-semibold text-xs shadow-sm h-8"
                  >
                    <ArrowLeftRight className="w-3.5 h-3.5 text-slate-500" />
                    Transfer Requests
                    {filteredIncoming.filter(r => r.status === 'Pending').length > 0 && (
                      <span className="bg-indigo-600 text-white text-[9px] px-1.5 py-0.5 rounded-full font-black">
                        {filteredIncoming.filter(r => r.status === 'Pending').length}
                      </span>
                    )}
                  </Button>
                )}
              </div>
          
          <div className="relative w-full sm:w-72">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input 
              placeholder="Search tasks, clients, or stages..." 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 h-9 text-sm bg-white border-slate-200 shadow-sm rounded-full focus-visible:ring-brand-teal"
            />
          </div>
        </div>

        {/* Bottom Filters Row */}
        <div className="px-4 pb-4 overflow-x-auto" style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}>
          <div className="flex items-center gap-2 min-w-max">


            {isAdminOrTL && (
              <div className="flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200/60 mr-2 shadow-sm">
                <button
                  onClick={() => setWorkScope('my')}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                    workScope === 'my'
                      ? 'bg-white text-slate-800 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  My Work
                </button>
                <button
                  onClick={() => setWorkScope('all')}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                    workScope === 'all'
                      ? 'bg-white text-slate-800 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  All Work
                </button>
              </div>
            )}

            <div className="relative w-[150px]">
              <Input 
                type="date"
                value={filterDate}
                onChange={(e) => setFilterDate(e.target.value)}
                className="h-9 text-sm bg-white pr-8 text-slate-600 rounded-md border-slate-200 focus-visible:ring-brand-teal"
              />
              {filterDate && (
                <Button 
                  variant="ghost" 
                  size="icon" 
                  className="absolute right-0 top-0 h-9 w-9 hover:bg-transparent"
                  onClick={() => setFilterDate('')}
                >
                  <X className="w-4 h-4 text-slate-400 hover:text-slate-600" />
                </Button>
              )}
            </div>

            {!hideTaskTypeFilter && (
              <Select value={filterTaskType} onValueChange={setFilterTaskType}>
                <SelectTrigger className="w-[150px] h-9 text-sm bg-white rounded-md border-slate-200 focus:ring-brand-teal">
                  <SelectValue placeholder="Task Type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Types</SelectItem>
                  <SelectItem value="content-calendar">Content Calendar</SelectItem>
                  <SelectItem value="other-work">Other Work</SelectItem>
                  <SelectItem value="digital-marketing">Digital Marketing</SelectItem>
                  <SelectItem value="follow-up">Follow-ups</SelectItem>
                </SelectContent>
              </Select>
            )}

            {!hideStageFilter && (
              <Select value={filterStage} onValueChange={setFilterStage}>
                <SelectTrigger className="w-[140px] h-9 text-sm bg-white rounded-md border-slate-200 focus:ring-brand-teal">
                  <SelectValue placeholder="Stage" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Stages</SelectItem>
                  {availableStages.includes('script') && <SelectItem value="script">Script</SelectItem>}
                  {availableStages.includes('shoot') && <SelectItem value="shoot">Shoot</SelectItem>}
                  {availableStages.includes('brand person') && <SelectItem value="brand person">Brand Person</SelectItem>}
                  {availableStages.includes('caption') && <SelectItem value="caption">Caption</SelectItem>}
                  {availableStages.includes('thumbnail') && <SelectItem value="thumbnail">Thumbnail</SelectItem>}
                  {availableStages.includes('editing') && <SelectItem value="editing">Editing</SelectItem>}
                  {availableStages.includes('post/graphics') && <SelectItem value="post/graphics">Post/Graphics</SelectItem>}
                  {availableStages.includes('approval') && <SelectItem value="approval">Approval</SelectItem>}
                  {availableStages.includes('posting') && <SelectItem value="posting">Posting</SelectItem>}
                  {availableStages.includes('follow-up') && <SelectItem value="follow-up">Follow-up</SelectItem>}
                  {availableStages.includes('whatsapp group') && <SelectItem value="whatsapp group">WhatsApp Group</SelectItem>}
                  {availableStages.includes('greetings msg') && <SelectItem value="greetings msg">Greetings Msg</SelectItem>}
                  {availableStages.includes('meeting') && <SelectItem value="meeting">Meeting</SelectItem>}
                  {availableStages.includes('content calendar') && <SelectItem value="content calendar">Content Calendar</SelectItem>}
                </SelectContent>
              </Select>
            )}

            {!hideProjectFilter && (
              <Select value={filterProject} onValueChange={setFilterProject}>
                <SelectTrigger className="w-[180px] h-9 text-sm bg-white rounded-md border-slate-200 focus:ring-brand-teal">
                  <SelectValue placeholder="Filter by Project" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Projects</SelectItem>
                  {projects.filter((p: any) => p.department === 'Creative' && p.status !== 'on-hold' && p.status !== 'onhold').map((project: any) => {
                    const client = clients.find(c => c.id === project.clientId);
                    const cName = client ? (client.companyName || client.clientName) : '';
                    return (
                      <SelectItem key={project.id} value={project.id}>{project.title} ({cName})</SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
            )}

            {(isAdminOrTL || defaultTaskType === 'dev-creative-work') && (
              <>
                {isAdminOrTL && currentUser?.role?.toLowerCase() === 'admin' && (
                  <SearchableSelect
                    options={[
                      { value: "all", label: "Assigned By: All" },
                      ...employees.map(emp => {
                        const empName = `${emp.firstName} ${emp.lastName}`;
                        return { value: `${empName}|${emp.id}`, label: empName };
                      })
                    ]}
                    value={filterAssigner}
                    onValueChange={setFilterAssigner}
                    placeholder="Assigned By"
                    triggerClassName="w-[160px] h-9 text-sm bg-white rounded-md border-slate-200 focus:ring-brand-teal"
                  />
                )}

                <SearchableSelect
                  options={[
                    { value: "all", label: "Assigned To: All" },
                    ...employees.map(emp => {
                      const empName = `${emp.firstName} ${emp.lastName}`;
                      return { value: `${empName}|${emp.id}`, label: empName };
                    })
                  ]}
                  value={filterAssignee}
                  onValueChange={setFilterAssignee}
                  placeholder="Assigned To"
                  triggerClassName="w-[160px] h-9 text-sm bg-white rounded-md border-slate-200 focus:ring-brand-teal"
                />
              </>
            )}
          </div>
        </div>
      </div>
      {loading ? (
        <div className="flex-1 flex items-center justify-center p-20">
          <Loader2 className="w-8 h-8 animate-spin text-brand-teal" />
        </div>
      ) : allPendingTasks.length === 0 ? (
        <div className="text-center py-20 flex-1 flex flex-col items-center justify-center">
          <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mb-4">
            <Filter className="w-8 h-8 text-slate-400" />
          </div>
          <h3 className="text-xl font-medium text-slate-700">No pending work found</h3>
          <p className="text-slate-500 mt-2 text-sm">Try adjusting your filters or search query, or maybe you're just all caught up!</p>
        </div>
      ) : (
        <div className="overflow-x-auto flex-1">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50/80 border-b border-slate-200 text-slate-500 text-xs uppercase tracking-wider font-semibold">
              <tr>
                <th className="px-6 py-4 whitespace-nowrap">Due Date</th>
                <th className="px-6 py-4 whitespace-nowrap">Client / Project</th>
                <th className="px-6 py-4 whitespace-nowrap">Stage</th>
                <th className="px-6 py-4 whitespace-nowrap">Task Details</th>
                <th className="px-6 py-4 whitespace-nowrap">Remark</th>
<th className="px-6 py-4 text-right whitespace-nowrap">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
               {allPendingTasks.map((item, idx) => {
                const isOverdue = new Date(item.deadline) < new Date(new Date().setHours(0,0,0,0));
                const todayStr = new Date().toISOString().split('T')[0];
                const isFollowupDueToday = item.isFollowup && item.deadline === todayStr;
                const transferReq = incomingRequests.find(r => r.taskId === item.id && (item.type === 'other-work' || r.stage === item.stage) && r.status === 'Accepted');
                return (
                  <tr 
                    key={`${item.id}-${item.type}-${idx}`} 
                    className={`hover:bg-slate-50/50 transition-colors group ${
                      isFollowupDueToday 
                        ? 'bg-amber-50/70 hover:bg-amber-100/60 border-l-4 border-l-amber-500 shadow-[inset_4px_0_0_0_#f59e0b]' 
                        : ''
                    }`}
                  >
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Badge variant={isOverdue ? "destructive" : "secondary"} className="text-xs px-2.5 py-1">
                        {formatDateToDDMMYYYY(item.deadline) || '-'}
                      </Badge>
                    </td>
                    <td className="px-6 py-4 font-medium text-slate-800">
                       {item.clientDisplayName}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Badge variant="outline" className="text-xs text-brand-teal border-brand-teal/30 bg-brand-teal/5">
                        {item.stage}
                      </Badge>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex flex-col">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold text-slate-800">{item.taskName}</span>
                          {item.monthYear && (
                            <span className="text-[10px] text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">
                              {item.monthYear}
                            </span>
                          )}
                          {transferReq && (
                            <span className="text-[10px] text-indigo-700 bg-indigo-50/80 px-2 py-0.5 rounded-full font-bold flex items-center gap-1 border border-indigo-100/60">
                              <ArrowLeftRight className="w-2.5 h-2.5" />
                              Transferred from {transferReq.senderName}
                            </span>
                          )}
                        </div>
                        {(item.assignerName || item.assigneeName) && (
                          <div className="text-xs text-slate-500 mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                            {item.assignerName && <div>Assigned by: <span className="font-medium text-slate-700">{item.assignerName}</span></div>}
                            {item.assigneeName && <div>Assigned to: <span className="font-medium text-slate-700">{item.assigneeName}</span></div>}
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-slate-600 max-w-[200px]">
                      {(() => {
                        const isApplicable = !item.remarkStage || (item.isOtherWork ? item.stage === item.remarkStage : isStageSubsequentOrEqual(item.stage, item.remarkStage, item.postReel));
                        const displayRemark = isApplicable ? item.remark : null;

                        return editingRemarkId === `${item.id}-${item.stage}` ? (
                          <div className="flex flex-col gap-1.5 min-w-[150px]">
                            <div className="flex items-center gap-1.5">
                              <Input 
                                value={editingRemarkValue}
                                onChange={(e) => setEditingRemarkValue(e.target.value)}
                                className="h-8 text-xs px-2 py-1 w-full focus-visible:ring-brand-teal"
                                autoFocus
                                placeholder="Type reason..."
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') handleSaveRemark(item.id, item.stage, item.isOtherWork);
                                  if (e.key === 'Escape') setEditingRemarkId(null);
                                }}
                              />
                              <button onClick={() => handleSaveRemark(item.id, item.stage, item.isOtherWork)} className="text-green-600 hover:bg-green-50 p-1.5 rounded transition-colors" title="Save">
                                <Check className="w-4 h-4" />
                              </button>
                              <button onClick={() => setEditingRemarkId(null)} className="text-slate-400 hover:bg-slate-100 p-1.5 rounded transition-colors" title="Cancel">
                                <X className="w-4 h-4" />
                              </button>
                            </div>
                            <label className="flex items-center gap-2 cursor-pointer mt-0.5">
                              <input 
                                type="checkbox" 
                                className="w-3 h-3 text-red-500 rounded border-slate-300 focus:ring-red-500 cursor-pointer"
                                checked={editingIsClientIssue}
                                onChange={(e) => setEditingIsClientIssue(e.target.checked)}
                              />
                              <span className="text-[10px] font-bold text-red-500 uppercase tracking-wider">Issue from client</span>
                            </label>
                          </div>
                        ) : (
                          <div 
                            className="cursor-pointer py-1 px-1 -mx-1 rounded hover:bg-slate-100 truncate flex-1 flex flex-col gap-0.5"
                            onClick={() => {
                              setEditingRemarkId(`${item.id}-${item.stage}`);
                              const remarkText = displayRemark || '';
                              if (remarkText.startsWith('[CLIENT ISSUE] ')) {
                                setEditingIsClientIssue(true);
                                setEditingRemarkValue(remarkText.replace('[CLIENT ISSUE] ', ''));
                              } else {
                                setEditingIsClientIssue(false);
                                setEditingRemarkValue(remarkText);
                              }
                            }}
                            title={displayRemark || ""}
                          >
                            {displayRemark ? (
                                displayRemark.startsWith('[CLIENT ISSUE] ') ? (
                                  <>
                                    <span className="text-[10px] font-bold text-red-500 uppercase tracking-wider flex items-center gap-1"><AlertCircle className="w-3 h-3" /> Client Issue</span>
                                    <span className="text-slate-600 truncate">{displayRemark.replace('[CLIENT ISSUE] ', '')}</span>
                                  </>
                                ) : (
                                  <span className="text-slate-600 truncate">{displayRemark}</span>
                                )
                            ) : (
                                "-"
                            )}
                          </div>
                        );
                      })()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <div className="flex justify-end items-center gap-2">
                        {(() => {
                          const uId = currentUser?.id || currentUser?._id;
                          const isAssigner = item.assignerId === uId;
                          const isAssignee = item.assigneeId === uId;
                          const fullRoles = ["admin", "manager", "social media manager", "smm", "director", "head", "super admin", "digital marketer", "digital marketing", "team leader", "sub admin", "sub-admin"];
                          const isAdminOrTL = currentUser?.name === 'Admin Admin' || currentUser?.role === 'HR' || fullRoles.includes(currentUser?.role?.toLowerCase() || '') || fullRoles.includes(currentUser?.designation?.toLowerCase() || '');
                          return item.isFollowup ? (
                            <>
                              <Button
                                onClick={() => router.push(`/work-management/smm`)}
                                variant="outline"
                                size="sm"
                                className="text-xs h-7 border-brand-teal text-brand-teal hover:bg-brand-teal/10 font-semibold rounded-lg flex items-center gap-1"
                              >
                                Perform Follow-up
                                <ArrowRight className="w-3.5 h-3.5" />
                              </Button>
                            </>
                          ) : item.isOtherWork ? (
                            <>
                              {(item.status === 'Pending' || item.status === 'In Progress') && isAssignee && (
                                <>
                                  {(isAssigner || isAdminOrTL || !item.assignerId || item.assignerId === item.assigneeId) ? (
                                    <Button 
                                      size="sm" 
                                      className="bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs h-7 rounded-lg flex items-center gap-1 shadow-sm"
                                      onClick={() => handleUpdateOtherWorkStatus(item.id, 'Approved')}
                                    >
                                      <Check className="w-3.5 h-3.5" />
                                      Completed
                                    </Button>
                                  ) : (
                                    <Button 
                                      size="sm" 
                                      className="bg-brand-teal hover:bg-brand-teal/90 text-white text-xs h-7"
                                      onClick={() => handleUpdateOtherWorkStatus(item.id, 'Ready for Review')}
                                    >
                                      Submit for Review
                                    </Button>
                                  )}
                                </>
                              )}
                              {item.status === 'Ready for Review' && (isAssigner || isAdminOrTL) && (
                                <>
                                  <Button 
                                    size="sm" 
                                    className="bg-green-600 hover:bg-green-700 text-white text-xs h-7"
                                    onClick={() => handleUpdateOtherWorkStatus(item.id, 'Approved')}
                                  >
                                    Approve
                                  </Button>
                                  <Button 
                                    size="sm" 
                                    variant="destructive"
                                    className="text-xs h-7"
                                    onClick={() => handleUpdateOtherWorkStatus(item.id, 'Pending')}
                                  >
                                    Reject
                                  </Button>
                                </>
                              )}
                              {getPendingTransferRequest(item) ? (
                                <span className="text-[10px] text-amber-600 bg-amber-50 border border-amber-200 px-2 py-1 rounded font-semibold whitespace-nowrap">
                                  Pending Transfer to {getPendingTransferRequest(item)?.receiverName}
                                </span>
                              ) : (
                                canTransferTask(item) && (
                                  <Button
                                    onClick={() => handleOpenTransferModal(item)}
                                    variant="ghost"
                                    size="icon"
                                    title="Transfer Task"
                                    className="h-8 w-8 text-indigo-600 hover:bg-indigo-50 hover:text-indigo-700"
                                  >
                                    <ArrowLeftRight className="w-4 h-4" />
                                  </Button>
                                )
                              )}
                              <Button
                                onClick={() => handleOpenLogs(item)}
                                variant="ghost"
                                size="icon"
                                title="View Logs"
                                className="h-8 w-8 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                              >
                                <History className="w-4 h-4" />
                              </Button>
                              {(isAssigner || isAdminOrTL) && (
                                <Button
                                  onClick={() => handleDeleteOtherWork(item.id)}
                                  variant="ghost"
                                  size="icon"
                                  title="Delete Task"
                                  className="h-8 w-8 text-red-500 hover:bg-red-50 hover:text-red-700"
                                >
                                  <Trash2 className="w-4 h-4" />
                                </Button>
                              )}
                            </>
                          ) : (
                            <>
                              {getPendingTransferRequest(item) ? (
                                <span className="text-[10px] text-amber-600 bg-amber-50 border border-amber-200 px-2 py-1 rounded font-semibold whitespace-nowrap">
                                  Pending Transfer to {getPendingTransferRequest(item)?.receiverName}
                                </span>
                              ) : (
                                canTransferTask(item) && (
                                  <Button
                                    onClick={() => handleOpenTransferModal(item)}
                                    variant="ghost"
                                    size="icon"
                                    title="Transfer Task"
                                    className="h-8 w-8 text-indigo-600 hover:bg-indigo-50 hover:text-indigo-700"
                                  >
                                    <ArrowLeftRight className="w-4 h-4" />
                                  </Button>
                                )
                              )}
                              <Button
                                onClick={() => handleOpenLogs(item)}
                                variant="ghost"
                                size="icon"
                                title="View Logs"
                                className="h-8 w-8 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                              >
                                <History className="w-4 h-4" />
                              </Button>
                              <Button
                                onClick={() => router.push(`/work-management/smm/${item.clientId}?projectId=${item.projectId}&highlightTask=${item.id}&highlightDate=${item.postingDate || ''}`)}
                                variant="ghost"
                                size="icon"
                                title="Show in Calendar"
                                className="h-8 w-8 text-brand-teal hover:bg-brand-teal/10 hover:text-brand-teal"
                              >
                                <CalendarIcon className="w-4 h-4" />
                              </Button>
                            </>
                          );
                        })()}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
        </>
      )}

      <Dialog open={logsDialogOpen} onOpenChange={setLogsDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col p-0 overflow-hidden bg-slate-50 border-0 shadow-2xl rounded-2xl">
          <DialogHeader className="px-6 py-6 bg-white flex flex-row items-center gap-4 shadow-[0_1px_3px_rgba(0,0,0,0.05)] relative z-10">
            <div className="w-14 h-14 rounded-full bg-brand-teal/10 flex items-center justify-center flex-shrink-0">
              <History className="w-6 h-6 text-brand-teal" />
            </div>
            <div className="flex flex-col gap-1 items-start text-left">
              <DialogTitle className="text-[22px] font-bold text-slate-900 m-0 leading-none">Row Activity History</DialogTitle>
              <p className="text-[13px] text-slate-500 italic m-0">Content Calendar Row</p>
            </div>
          </DialogHeader>
          <div className="p-6 overflow-y-auto flex-1 bg-slate-50">
            {currentLogs.length === 0 ? (
              <p className="text-sm text-slate-500 text-center py-8">No activity logs found for this task.</p>
            ) : (
              <div className="relative pl-1">
                {/* Vertical Timeline Line */}
                <div className="absolute left-[9px] top-6 bottom-6 w-px bg-slate-200 z-0" />
                
                <div className="space-y-4">
                  {currentLogs.slice().reverse().map((log: any, i: number) => {
                    let dateStr = log.timestamp;
                    try {
                      dateStr = new Intl.DateTimeFormat('en-US', {
                        month: 'short', day: 'numeric', year: 'numeric',
                        hour: 'numeric', minute: '2-digit', hour12: true
                      }).format(new Date(log.timestamp));
                    } catch (e) {}

                    const isCreated = log.action?.toLowerCase().includes('create') || log.details?.toLowerCase().includes('create');
                    const badgeColor = isCreated ? "bg-blue-100 text-blue-600" : "bg-emerald-100 text-emerald-600";
                    const badgeText = isCreated ? "CREATED" : "UPDATED";
                    
                    const detailsList = log.details ? log.details.split(', ').filter((d: string) => d.trim() !== '') : [];

                    return (
                      <div key={i} className="flex gap-6 relative z-10">
                        <div className="flex flex-col items-center mt-[22px]">
                          <div className="w-3 h-3 rounded-full border-2 border-brand-teal bg-white" />
                        </div>
                        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-[0_1px_2px_rgba(0,0,0,0.02)] flex-1">
                          <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center gap-3">
                              <span className="font-bold text-slate-900 text-[15px]">{log.userName || 'Admin'}</span>
                              <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${badgeColor} uppercase tracking-wider`}>
                                {badgeText}
                              </span>
                            </div>
                            <div className="text-xs text-slate-400 font-medium">
                              {dateStr}
                            </div>
                          </div>
                          <ul className="space-y-1.5">
                            {detailsList.length > 0 ? detailsList.map((detail: string, idx: number) => (
                              <li key={idx} className="flex items-start gap-2 text-[13px] text-slate-600">
                                <span className="text-slate-400 mt-[3px] text-lg leading-none">•</span>
                                <span className="leading-relaxed">{detail}</span>
                              </li>
                            )) : (
                              <li className="flex items-start gap-2 text-[13px] text-slate-600">
                                <span className="text-slate-400 mt-[3px] text-lg leading-none">•</span>
                                <span className="leading-relaxed">{log.action || 'No details provided'}</span>
                              </li>
                            )}
                          </ul>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
          <div className="p-4 bg-white border-t border-slate-200 flex justify-end">
            <Button onClick={() => setLogsDialogOpen(false)} className="bg-brand-teal hover:bg-brand-teal/90 text-white px-8 rounded-lg font-bold shadow-sm h-10">
              Close
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={isTransferModalOpen} onOpenChange={setIsTransferModalOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-slate-800 flex items-center gap-2">
              <ArrowLeftRight className="w-5 h-5 text-brand-teal" />
              Transfer Task
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="bg-slate-50 p-3 rounded-lg border border-slate-100 text-xs space-y-1.5">
              <div>Task Name: <span className="font-semibold text-slate-800">{transferringTask?.taskName}</span></div>
              <div>Stage: <span className="font-semibold text-slate-800">{transferringTask?.stage}</span></div>
              <div>Client: <span className="font-semibold text-slate-800">{transferringTask?.clientDisplayName}</span></div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-semibold text-slate-700 block">Select Employee to Transfer To</label>
              <Select value={selectedReceiverId} onValueChange={setSelectedReceiverId}>
                <SelectTrigger className="w-full focus:ring-brand-teal focus:border-brand-teal">
                  <SelectValue placeholder="Choose employee..." />
                </SelectTrigger>
                <SelectContent>
                  {(() => {
                    const getTaskDepartment = () => {
                      if (!transferringTask) return "";
                      
                      // Prioritize the assignee's department since the task is assigned to them
                      if (transferringTask.assigneeId) {
                        const assignee = employees.find((e: any) => e.id === transferringTask.assigneeId);
                        if (assignee && assignee.department) {
                          return assignee.department;
                        }
                      }

                      if (!transferringTask.isOtherWork) return "Creative";
                      const tType = transferringTask.type?.toLowerCase();
                      if (tType === 'development') return "Development";
                      if (tType === 'digital-marketing') return "Digital Marketing";
                      if (tType === 'creative' || tType === 'content-calendar') return "Creative";
                      const userDept = currentUser?.department;
                      if (userDept && userDept.toLowerCase() !== 'admin' && userDept.toLowerCase() !== 'hr') {
                        return userDept;
                      }
                      return "Creative";
                    };
                    const targetDept = getTaskDepartment();
                    
                    const isCreativeDept = targetDept.toLowerCase() === 'creative' || targetDept.toLowerCase() === 'smm' || targetDept.toLowerCase() === 'social media marketing' || targetDept.toLowerCase() === 'graphics';
                    const isDMDept = targetDept.toLowerCase() === 'digital-marketing' || targetDept.toLowerCase() === 'digital marketing' || targetDept.toLowerCase() === 'dm';
                    const isDevDept = targetDept.toLowerCase() === 'development' || targetDept.toLowerCase() === 'dev';

                    return employees
                      .filter((emp: any) => {
                        return emp.id !== currentUser?.id;
                      })
                      .map((emp: any) => {
                        const name = `${emp.firstName} ${emp.lastName || ''}`.trim();
                        return (
                          <SelectItem key={emp.id} value={emp.id}>
                            {name} ({emp.role || 'Employee'})
                          </SelectItem>
                        );
                      });
                  })()}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex justify-end gap-2.5 pt-2 border-t border-slate-100">
            <Button variant="outline" onClick={() => setIsTransferModalOpen(false)}>
              Cancel
            </Button>
            <Button 
              className="bg-brand-teal hover:bg-brand-teal/90 text-white font-semibold"
              onClick={handleSendTransferRequest}
            >
              Send Request
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
