'use client'

import React, { useState, useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { 
  Loader2, 
  AlertCircle,
  Calendar as CalendarIcon,
  Briefcase,
  Code,
  Megaphone,
  UserCheck,
  X,
  Plus,
  CheckCircle2,
  Clock,
  ExternalLink
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SearchableSelect } from '@/components/ui/searchable-select'
import { Calendar as DayCalendar } from '@/components/ui/calendar'
import { format } from 'date-fns'
import { API_URL } from '@/lib/config'
import { PageHeader } from '@/components/common/PageHeader'
import { toast } from 'sonner'

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

const isStageSubsequentOrEqual = (stageName: string, remarkStageName?: string, postReel?: string) => {
  if (!remarkStageName) return true;
  const reelStages = ['Script', 'Shoot', 'Brand Person', 'Editing', 'Caption', 'Thumbnail', 'Approval', 'Posting'];
  const postStages = ['Post/Graphics', 'Brand Person', 'Caption', 'Approval', 'Posting'];
  const storyStages = ['Editing', 'Approval'];
  
  const stages = postReel === 'Post' ? postStages : postReel === 'Story' ? storyStages : reelStages;
  const remarkIdx = stages.findIndex(s => s.toLowerCase() === remarkStageName.toLowerCase());
  const stageIdx = stages.findIndex(s => s.toLowerCase() === stageName.toLowerCase());
  
  if (remarkIdx === -1 || stageIdx === -1) return true;
  return stageIdx >= remarkIdx;
};

export interface MyTasksViewProps {
  targetUserId?: string;
  isEmbedded?: boolean;
  targetDate?: string;
}

export function MyTasksView({ targetUserId, isEmbedded = false, targetDate }: MyTasksViewProps) {
  const router = useRouter()
  const [currentUser, setCurrentUser] = useState<any>(() => {
    if (typeof window !== 'undefined') {
      const stored = localStorage.getItem('user')
      if (stored) {
        try { return JSON.parse(stored) } catch (e) { return null }
      }
    }
    return null
  })
  
  // Tasks states
  const [tasks, setTasks] = useState<any[]>([]) // General & HR Tasks
  const [wmTasks, setWmTasks] = useState<any[]>([]) // Development tasks
  const [entries, setEntries] = useState<any[]>([]) // SMM Creative Entries
  const [otherWork, setOtherWork] = useState<any[]>([]) // SMM Other Work
  const [projects, setProjects] = useState<any[]>([])
  const [clients, setClients] = useState<any[]>([])
  const [employees, setEmployees] = useState<any[]>([])
  const [leads, setLeads] = useState<any[]>([])
  const [dailyReports, setDailyReports] = useState<any[]>([])
  const [projectRemarks, setProjectRemarks] = useState<any[]>([])
  
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('today')
  const [activeDateRange, setActiveDateRange] = useState<{from: Date | undefined, to?: Date | undefined} | undefined>(
    targetDate ? { from: parseLocalDate(targetDate) } : undefined
  )
  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string>('all')

  useEffect(() => {
    if (!currentUser && typeof window !== 'undefined') {
      const storedUser = localStorage.getItem('user')
      if (storedUser) {
        try { setCurrentUser(JSON.parse(storedUser)) } catch (e) {}
      } else if (!targetUserId) {
        router.push('/login')
      }
    } else if (!currentUser && !targetUserId) {
      router.push('/login')
    }
  }, [router, targetUserId, currentUser])

  const fetchData = async () => {
    setLoading(true)
    try {
      let userObj = currentUser
      if (!userObj && typeof window !== 'undefined') {
        const storedUser = localStorage.getItem('user')
        if (storedUser) {
          try { userObj = JSON.parse(storedUser) } catch (e) {}
        }
      }
      const uId = targetUserId || userObj?.id || ''
      const role = userObj?.role || ''
      const res = await fetch(`${API_URL}/my-tasks-view-data?userId=${uId}&role=${role}`, { cache: 'no-store' })
      if (res.ok) {
        const data = await res.json()
        setTasks(data.tasks || [])
        setWmTasks(data.wmTasks || [])
        setEntries(data.contentCalendar || [])
        setOtherWork(data.otherWork || [])
        setProjects(data.projects || [])
        setClients(data.clients || [])
        setEmployees(data.employees || [])
        setLeads(data.leads || [])
        setDailyReports(data.dailyReports || [])
        setProjectRemarks(data.projectRemarks || [])
      }
    } catch (err) {
      console.error('Error fetching data:', err)
      toast.error('Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let userObj = currentUser
    if (!userObj && typeof window !== 'undefined') {
      const storedUser = localStorage.getItem('user')
      if (storedUser) {
        try { userObj = JSON.parse(storedUser) } catch (e) {}
      }
    }
    if (userObj || targetUserId) {
      fetchData()
    }
  }, [targetUserId])

  const handleMarkComplete = async (task: any) => {
    try {
      let url = ''
      let payload: any = {}

      if (task.sourceType === 'general-task') {
        url = `${API_URL}/tasks/${task.id}`
        payload = { status: 'completed', performedBy: currentUser?.id, userName: currentUser?.name }
      } else if (task.sourceType === 'wm-task') {
        url = `${API_URL}/wm-tasks/${task.id}`
        payload = { status: 'completed', updatedBy: currentUser?.name }
      } else if (task.sourceType === 'smm-other') {
        url = `${API_URL}/other-work/${task.id}`
        payload = { status: 'Approved', updatedBy: currentUser?.name }
      } else if (task.sourceType === 'smm-creative') {
        url = `${API_URL}/content-calendar/${task.originalTask?.id || task.id}`
        const stage = task.stage
        if (stage === 'Script') payload = { scriptLink: 'Completed', status: 'Script Done' }
        else if (stage === 'Shoot') payload = { shootLink: 'Completed', status: 'Shoot Done' }
        else if (stage === 'Caption') payload = { caption: 'Completed' }
        else if (stage === 'Thumbnail') payload = { thumbnailLink: 'Completed' }
        else if (stage === 'Editing' || stage === 'Post/Graphics') payload = { finalPostLink: 'Completed', finalReelLink: 'Completed' }
        else if (stage === 'Approval') payload = { isApproved: 'Yes' }
        else if (stage === 'Posting') payload = { postingLinkOfIg: 'Completed' }
        else payload = { status: 'Completed' }
      } else {
        url = `${API_URL}/tasks/${task.id}`
        payload = { status: 'completed' }
      }

      const res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })

      if (res.ok) {
        toast.success(`Task marked as completed!`)
        fetchData()
      } else {
        toast.error(`Failed to mark task as completed.`)
      }
    } catch (err) {
      console.error(err)
      toast.error(`Error marking task as completed.`)
    }
  }

  const getRedirectUrl = (task: any) => {
    if (task.sourceType === 'smm-other' || task.isOtherWork || task.originalTask?.isOtherWork) {
      return null;
    }
    if (task.sourceType === 'wm-task' || task.department === 'Development') {
      return '/work-management/development';
    }
    if (task.sourceType === 'smm-creative' || task.sourceType === 'smm-followup' || task.department === 'Social Media Management' || task.department === 'Digital Marketing' || task.sourceType === 'dm-missing-metric') {
      const isDM = task.department?.toLowerCase() === 'digital marketing' || task.originalTask?.taskType === 'digital-marketing' || task.originalTask?.taskType === 'dm-other-work' || task.sourceType === 'dm-missing-metric';
      const base = isDM ? '/work-management/digital-marketing' : '/work-management/smm';
      
      const clientId = task.clientId || task.originalTask?.clientId;
      const projectId = task.projectId || task.originalTask?.projectId;
      
      if (clientId) {
        let url = `${base}/${clientId}`;
        if (projectId) {
          url += `?projectId=${projectId}`;
        }
        return url;
      }
      return base;
    }
    if (task.sourceType === 'general-task') {
      const dept = task.originalTask?.department;
      if (task.department === 'HR Tasks' || (dept && dept.toUpperCase() === 'HR')) {
        return '/work-management/hr-tasks';
      }
      return '/tasks';
    }
    return null;
  };

  // Process all tasks assigned to the current user
  const effectiveUserId = targetUserId || (selectedEmployeeId !== 'all' ? selectedEmployeeId : currentUser?.id);
  const allConsolidatedTasks = useMemo(() => {
    const uId = effectiveUserId;
    if (!uId) return []

    const consolidated: any[] = []

    // 1. General & HR Tasks
    tasks.forEach(t => {
      let assIds: string[] = []
      if (Array.isArray(t.assignedToIds)) {
        assIds = t.assignedToIds
      } else if (typeof t.assignedToIds === 'string') {
        try {
          assIds = t.assignedToIds.split(',').map((id: string) => id.trim()).filter(Boolean)
        } catch (e) {
          assIds = t.assignedToIds.split(',').map((id: string) => id.trim()).filter(Boolean)
        }
      }

      const isAssigned = t.assignedToId === uId || assIds.includes(uId);

      if (isAssigned) {
        let isProjectOnHold = false;
        if (t.projectId) {
          const assocProject = projects.find(p => p.id === t.projectId);
          isProjectOnHold = assocProject && (assocProject.status === 'on-hold' || assocProject.status === 'onhold' || assocProject.status?.toLowerCase() === 'on-hold');
        }
        
        if (!isProjectOnHold) {
          const isHR = t.department === 'HR' || t.department?.toUpperCase() === 'HR';
          consolidated.push({
            id: t.id,
            title: t.title,
            description: t.description,
            dueDate: t.dueDate ? (t.dueDate.includes('T') ? t.dueDate.split('T')[0] : t.dueDate) : '',
            priority: t.priority || 'medium',
            status: t.status,
            frequency: t.frequency || 'one-time',
            department: isHR ? 'HR Tasks' : 'General Tasks',
            sourceType: 'general-task',
            originalTask: t
          });
        }
      }
    })

    // 2. Development Tasks
    wmTasks.forEach(t => {
      const isAssigned = t.assignedToId === uId || t.assignedToIds?.includes(uId)
      if (isAssigned) {
        const assocProject = projects.find(p => p.id === t.projectId)
        const isProjectOnHold = assocProject && (assocProject.status === 'on-hold' || assocProject.status === 'onhold' || assocProject.status?.toLowerCase() === 'on-hold')
        
        if (!isProjectOnHold) {
          consolidated.push({
            id: t.id,
            title: t.title,
            description: t.description,
            dueDate: t.dueDate ? (t.dueDate.includes('T') ? t.dueDate.split('T')[0] : t.dueDate) : '',
            priority: t.priority || 'medium',
            status: t.status,
            department: 'Development',
            sourceType: 'wm-task',
            projectName: t.projectName || assocProject?.title || 'Unknown Project',
            originalTask: t
          })
        }
      }
    })

    entries.forEach(entry => {
      const client = clients.find(c => c.id === entry.clientId)
      let assocProject = null;
      if (entry.projectId) {
        assocProject = projects.find(p => p.id === entry.projectId);
      }
      if (!assocProject) {
        assocProject = projects.find(p => 
          p.clientId === entry.clientId && 
          (p.department === 'Creative' || p.department?.toLowerCase() === 'smm' || p.department?.toLowerCase() === 'social media management')
        );
      }

      const isProjectOnHold = assocProject && (assocProject.status === 'on-hold' || assocProject.status === 'onhold' || assocProject.status?.toLowerCase() === 'on-hold');
      
      if (!isProjectOnHold) {
        const checkAndAddCreativeTask = (stageName: string, deadline: string, isDone: boolean) => {
          let assigneeId = null
          if (stageName === 'Script') assigneeId = entry.assignedScriptwriterId || assocProject?.assignedScriptwriterId || client?.assignedScriptwriterId
          if (stageName === 'Shoot') assigneeId = entry.assignedShooterId || assocProject?.assignedShooterId || client?.assignedShooterId
          if (stageName === 'Caption') assigneeId = entry.assignedCaptionWriterId || assocProject?.assignedCaptionWriterId || client?.assignedCaptionWriterId
          if (stageName === 'Thumbnail') assigneeId = entry.assignedThumbnailDesignerId || assocProject?.assignedThumbnailDesignerId || client?.assignedThumbnailDesignerId
          if (stageName === 'Editing') {
            if (entry.postReel === 'Post') {
              assigneeId = entry.assignedPostDesignerId || assocProject?.assignedPostDesignerId || client?.assignedPostDesignerId
            } else {
              assigneeId = entry.assignedReelEditorId || assocProject?.assignedReelEditorId || client?.assignedReelEditorId
            }
          }
          if (stageName === 'Approval') assigneeId = entry.assignedApproverId || assocProject?.assignedApproverId || client?.assignedApproverId
          if (stageName === 'Posting') assigneeId = entry.assignedPosterId || assocProject?.assignedPosterId || client?.assignedPosterId

          if (assigneeId === uId && !isDone && deadline) {
            const creatorName = entry.logs?.[0]?.userName || 'Admin'
            const empName = employees.find(e => e.id === assigneeId)?.name || currentUser?.name || 'User'
            const enrichedEntry = { ...entry, assignerName: creatorName, assigneeName: empName }

            const cleanConcept = (entry.concept && entry.concept.trim() !== '-') ? entry.concept.trim() : '';
            const cleanTopic = (entry.topic && entry.topic.trim() !== '-') ? entry.topic.trim() : '';

            consolidated.push({
              id: `${entry.id}-${stageName}`,
              title: cleanConcept || cleanTopic || (entry.postReel ? `${entry.postReel} Content` : 'SMM Task'),
              clientDisplayName: assocProject ? `${assocProject.title}${client ? ` (${client.companyName || client.clientName})` : ''}` : (client ? (client.companyName || client.clientName) : 'Unknown Client'),
              stage: stageName,
              dueDate: deadline.includes('T') ? deadline.split('T')[0] : deadline,
              priority: 'medium',
              status: 'todo',
              department: 'Social Media Management',
              sourceType: 'smm-creative',
              clientId: entry.clientId || client?.id,
              projectId: entry.projectId || assocProject?.id,
              originalTask: enrichedEntry
            })
          }
        }

        const isPost = entry.postReel === 'Post';
        const isStory = entry.postReel === 'Story';

        if (!isPost && !isStory && entry.scriptDate && entry.scriptDate !== '-') {
          checkAndAddCreativeTask('Script', entry.scriptDate, !!entry.scriptLink)
        }
        if (!isPost && !isStory && entry.shootDate && entry.shootDate !== '-') {
          checkAndAddCreativeTask('Shoot', entry.shootDate, !!entry.shootLink)
        }
        
        // Brand Person Check
        if (!isStory && entry.assignedBrandPersonIds && (!entry.shootLink || entry.shootLink === '-')) {
          const bpIdsRaw = entry.assignedBrandPersonIds;
          const bpIds = Array.isArray(bpIdsRaw) ? bpIdsRaw : (typeof bpIdsRaw === 'string' ? bpIdsRaw.split(',').map((id: string) => id.trim()).filter(Boolean) : []);
          bpIds.forEach((bpId: string) => {
            if (bpId === uId) {
              const taskDeadline = entry.shootDate || entry.postingDate || (entry.monthYear ? `${entry.monthYear}-28` : new Date().toISOString().split('T')[0]);
              
              const creatorName = entry.logs?.[0]?.userName || 'Admin'
              const empName = employees.find(e => e.id === bpId)?.name || currentUser?.name || 'User'
              const enrichedEntry = { ...entry, assignerName: creatorName, assigneeName: empName }
              
              const cleanConcept = (entry.concept && entry.concept.trim() !== '-') ? entry.concept.trim() : '';
              const cleanTopic = (entry.topic && entry.topic.trim() !== '-') ? entry.topic.trim() : '';

              consolidated.push({
                id: `${entry.id}-BrandPerson`,
                title: cleanConcept || cleanTopic || (entry.postReel ? `${entry.postReel} Content` : 'SMM Task'),
                clientDisplayName: assocProject ? `${assocProject.title}${client ? ` (${client.companyName || client.clientName})` : ''}` : (client ? (client.companyName || client.clientName) : 'Unknown Client'),
                stage: 'Brand Person',
                dueDate: taskDeadline,
                priority: 'medium',
                status: 'todo',
                department: 'Social Media Management',
                sourceType: 'smm-creative',
                clientId: entry.clientId || client?.id,
                projectId: entry.projectId || assocProject?.id,
                originalTask: enrichedEntry
              })
            }
          })
        }

        const captionDate = entry.captionDate || entry.editingStart;
        if (!isStory && captionDate && captionDate !== '-') {
          checkAndAddCreativeTask('Caption', captionDate, !!entry.caption)
        }
        if (!isPost && !isStory && entry.thumbnailDate && entry.thumbnailDate !== '-') {
          checkAndAddCreativeTask('Thumbnail', entry.thumbnailDate, !!entry.thumbnailLink)
        }
        if (entry.editingStart && entry.editingStart !== '-') {
          const isDone = isPost ? !!entry.finalPostLink : !!entry.finalReelLink
          checkAndAddCreativeTask('Editing', entry.editingStart, isDone)
        }
        if (entry.approval && entry.approval !== '-') {
          checkAndAddCreativeTask('Approval', entry.approval, entry.isApproved === 'Yes')
        }
        if (!isStory && entry.postingDate && entry.postingDate !== '-') {
          checkAndAddCreativeTask('Posting', entry.postingDate, !!entry.postingLinkOfIg)
        }
      }
    })

    // 4. SMM Other Work
    const targetEmp = employees.find(e => String(e.id) === String(uId)) || (uId === currentUser?.id ? currentUser : null);
    const targetEmpName = targetEmp ? (targetEmp.name || `${targetEmp.firstName || ''} ${targetEmp.lastName || ''}`.trim()) : '';

    otherWork.forEach(ow => {
      const isAssignee = String(ow.assigneeId) === String(uId) || (targetEmpName && ow.assigneeName && ow.assigneeName.toLowerCase().includes(targetEmpName.toLowerCase()));
      const isAssigner = !targetUserId && (String(ow.assignerId) === String(uId) || (targetEmpName && ow.assignerName && ow.assignerName.toLowerCase().includes(targetEmpName.toLowerCase())));
      if ((isAssignee || isAssigner) && ow.status !== 'Approved') {
        let isProjectOnHold = false;
        if (ow.projectId) {
          const assocProject = projects.find(p => p.id === ow.projectId);
          isProjectOnHold = assocProject && (assocProject.status === 'on-hold' || assocProject.status === 'onhold' || assocProject.status?.toLowerCase() === 'on-hold');
        }
        
        if (!isProjectOnHold) {
          const assignerEmp = employees.find(e => e.id === ow.assignerId);
          const assigneeEmp = employees.find(e => e.id === ow.assigneeId);
          const creatorName = assignerEmp ? (assignerEmp.name || `${assignerEmp.firstName || ''} ${assignerEmp.lastName || ''}`.trim()) : (ow.assignerName || ow.logs?.[0]?.userName || 'Manager');
          const empName = assigneeEmp ? (assigneeEmp.name || `${assigneeEmp.firstName || ''} ${assigneeEmp.lastName || ''}`.trim()) : (ow.assigneeName || currentUser?.name || 'User');
          const enrichedOw = { ...ow, assignerName: creatorName, assigneeName: empName };

          consolidated.push({
            id: ow.id,
            title: ow.title,
            clientDisplayName: ow.taskType === 'digital-marketing' ? 'Digital Marketing' : 'Other Work',
            description: ow.description || 'SMM other work task',
            dueDate: ow.deadline ? (ow.deadline.includes('T') ? ow.deadline.split('T')[0] : ow.deadline) : '',
            priority: ow.priority || 'medium',
            status: ow.status,
            stage: ow.status,
            department: ow.taskType === 'digital-marketing' ? 'Digital Marketing' : 'Social Media Management',
            sourceType: 'smm-other',
            clientId: ow.clientId,
            projectId: ow.projectId,
            originalTask: enrichedOw
          })
        }
      }
    })
    
    // 5. SMM Client Project Follow-ups
    projects.forEach(project => {
      const isCreative = project.department === 'Creative' || project.department?.toLowerCase() === 'smm'
      if (isCreative) {
        const isProjectOnHold = project.status === 'on-hold' || project.status === 'onhold' || project.status?.toLowerCase() === 'on-hold'
        if (!isProjectOnHold && project.nextFollowupDate) {
          const client = clients.find(c => c.id === project.clientId)
          const followUpAssigneeId = project.assignedFollowUpId || client?.assignedFollowUpId || project.teamLeaderId
          if (followUpAssigneeId === uId) {
            const nextDate = project.nextFollowupDate.split("T")[0].split(" ")[0]
            
            const empName = employees.find(e => e.id === followUpAssigneeId)?.name || currentUser?.name || 'User';
            const tlName = employees.find(e => e.id === project.teamLeaderId)?.name || 'Manager';
            const enrichedProject = { ...project, assignerName: tlName, assigneeName: empName };

            consolidated.push({
              id: `${project.id}-Followup`,
              title: `Follow-up: ${project.title || client?.companyName || 'Project'}`,
              clientDisplayName: client?.companyName || 'Unknown Client',
              stage: 'Follow-up',
              dueDate: nextDate,
              priority: 'medium',
              status: 'todo',
              department: 'Social Media Management',
              sourceType: 'smm-followup',
              originalTask: enrichedProject
            })
          }
        }
      }
    })

    // 6. Digital Marketing Missing Daily Metrics
    const normalizeDate = (d: string) => d ? d.split(" ")[0].split("T")[0] : "";
    const getPastDates = (maxDays = 6) => {
      const dates = [];
      for (let i = -1; i <= maxDays; i++) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        dates.push(d.toISOString().split("T")[0]);
      }
      return dates;
    };

    clients.forEach((client) => {
      const clientProjects = projects.filter((p) => p.clientId === client.id && p.department?.toLowerCase() === "digital marketing" && p.status?.toLowerCase() === "active");
      const proj = clientProjects[0];

      if (proj) {
        const dates = getPastDates(6);
        dates.forEach(dateStr => {
          const report = dailyReports.find(r => r.clientId === client.id && normalizeDate(r.date) === dateStr);
          const isFilled = (val: any) => val !== undefined && val !== null && val !== "";
          const isClientIssue = report?.remarks && report.remarks.toString().includes("[CLIENT ISSUE]");

          const metricsRecord = projectRemarks.find(r => r.projectId === proj.id && normalizeDate(r.date) === dateStr);
          const hasMetrics = !!metricsRecord && (!metricsRecord.userRemark?.includes("[CLIENT ISSUE]") && !metricsRecord.clientRemark?.includes("[CLIENT ISSUE]") && !metricsRecord.remark?.includes("[CLIENT ISSUE]"));

          let dayTasks = [
            { id: "reach", name: "Reach", assigneeId: proj.assignedEmployeeId, date: dateStr, existingRemark: report?.remarks },
            { id: "impression", name: "Impressions", assigneeId: proj.assignedEmployeeId, date: dateStr, existingRemark: report?.remarks },
            { id: "leads", name: "Leads", assigneeId: proj.assignedEmployeeId, date: dateStr, existingRemark: report?.remarks },
            { id: "spend", name: "Spend", assigneeId: proj.assignedEmployeeId, date: dateStr, existingRemark: report?.remarks },
            { id: "cpl", name: "Cost Metric", assigneeId: proj.assignedEmployeeId, date: dateStr, existingRemark: report?.remarks },
            { id: "revenue", name: "Revenue", assigneeId: proj.revenueAssigneeId, date: dateStr, existingRemark: metricsRecord?.remark },
            { id: "follower", name: "Follower", assigneeId: proj.followerAssigneeId, date: dateStr, existingRemark: metricsRecord?.remark },
          ].filter(t => t.assigneeId);

          if (report && !isClientIssue) {
            if (isFilled(report.reach)) dayTasks = dayTasks.filter(t => t.id !== "reach");
            if (isFilled(report.impression)) dayTasks = dayTasks.filter(t => t.id !== "impression");
            if (isFilled(report.leads)) dayTasks = dayTasks.filter(t => t.id !== "leads");
            if (isFilled(report.spend)) dayTasks = dayTasks.filter(t => t.id !== "spend");
            if (isFilled(report.cpl)) dayTasks = dayTasks.filter(t => t.id !== "cpl");
          }
          
          if (hasMetrics) {
            dayTasks = dayTasks.filter(t => !["revenue", "follower"].includes(t.id));
          }

          dayTasks.forEach(t => {
            if (String(t.assigneeId) === String(uId)) {
              const assigneeEmp = employees.find(e => String(e.id) === String(t.assigneeId));
              const empName = assigneeEmp ? (assigneeEmp.name || `${assigneeEmp.firstName || ''} ${assigneeEmp.lastName || ''}`.trim()) : 'User';
              
              consolidated.push({
                id: `${proj.id}-missing-${t.id}-${dateStr}`,
                title: `Missing Metric: ${t.name} for ${dateStr}`,
                clientDisplayName: 'Digital Marketing',
                projectName: `${proj.title} (${client.companyName || client.clientName || 'Unknown Client'})`,
                description: `Please fill in the daily report for ${t.name}.`,
                dueDate: dateStr,
                priority: 'high',
                status: 'todo',
                stage: 'Pending',
                department: 'Digital Marketing',
                sourceType: 'dm-missing-metric',
                clientId: client.id,
                projectId: proj.id,
                originalTask: { ...t, assignerName: 'System', assigneeName: empName, taskType: 'digital-marketing', clientId: client.id, projectId: proj.id }
              });
            }
          });
        });
      }
    });

      return consolidated
    }, [tasks, wmTasks, entries, otherWork, projects, clients, effectiveUserId, employees, currentUser, dailyReports, projectRemarks])

    const kpiData = useMemo(() => {
      let total = 0;
      let inProgress = 0;
      let pendingReview = 0;
      let overdue = 0;

      const today = new Date();
      today.setHours(0,0,0,0);

      allConsolidatedTasks.forEach(t => {
        const status = t.status?.toLowerCase() || '';
        if (status === 'completed' || status === 'approved') return;

        total++;
        if (status === 'in progress' || status === 'in_progress' || status === 'in-progress') inProgress++;
        else if (status === 'pending review' || status === 'pending_review' || status === 'ready for review' || status === 'ready_for_review' || status === 'review') pendingReview++;

        const dueDateStr = t.dueDate || t.postingDate;
        if (dueDateStr) {
          const dueDate = parseLocalDate(dueDateStr);
          if (dueDate < today) overdue++;
        }
      });

      return { total, inProgress, pendingReview, overdue };
    }, [allConsolidatedTasks]);

    const currentEmp = useMemo(() => employees.find(e => e.id === effectiveUserId) || currentUser, [employees, effectiveUserId, currentUser]);
    const isSalesDept = currentEmp?.department?.toLowerCase() === 'sales';

    const salesKpiData = useMemo(() => {
      if (!isSalesDept) return null;
      let targetDateStr = "";
      if (activeDateRange?.from) {
        targetDateStr = format(activeDateRange.from, 'yyyy-MM-dd');
      } else {
        targetDateStr = format(new Date(), 'yyyy-MM-dd');
      }

      const normalizeDate = (d: string) => d ? d.split(" ")[0].split("T")[0] : "";
      
      const userLeads = leads.filter(l => {
        if (Array.isArray(l.assignedTo)) {
          return l.assignedTo.some((a: any) => a.id === effectiveUserId || a.name === currentEmp?.firstName + " " + currentEmp?.lastName || a.name === currentEmp?.name);
        }
        return l.assignedTo === effectiveUserId || l.assignedTo === currentEmp?.name || l.assignedTo === currentEmp?.firstName + " " + currentEmp?.lastName;
      });

      const totalLeadsToday = userLeads.filter(l => normalizeDate(l.date) === targetDateStr).length;
      const hotLeads = userLeads.filter(l => l.isHot && l.status !== 'Client Won' && l.status !== 'Client Loss').length;
      const convertedLeads = userLeads.filter(l => l.status === 'Client Won' && normalizeDate(l.closedDate) === targetDateStr).length;
      
      let followupsToday = 0;
      userLeads.forEach(l => {
        if (Array.isArray(l.followUps)) {
          followupsToday += l.followUps.filter((f: any) => normalizeDate(f.date) === targetDateStr || normalizeDate(f.nextFollowUpDate) === targetDateStr).length;
        }
      });

      return { totalLeadsToday, hotLeads, convertedLeads, followupsToday };
    }, [leads, effectiveUserId, currentEmp, activeDateRange, isSalesDept]);
  
  const filteredByDateTasks = useMemo(() => {
    if (!activeDateRange?.from) return allConsolidatedTasks
    
    const from = new Date(activeDateRange.from)
    from.setHours(0, 0, 0, 0)
    
    const to = activeDateRange.to ? new Date(activeDateRange.to) : null
    
    if (to) {
      to.setHours(0, 0, 0, 0)
      return allConsolidatedTasks.filter(t => {
        if (!t.dueDate) return false
        const taskDateObj = parseLocalDate(t.dueDate)
        return taskDateObj >= from && taskDateObj <= to
      })
    }
    
    return allConsolidatedTasks
  }, [allConsolidatedTasks, activeDateRange])

  // Filter tasks into Today, Pending, Upcoming using SMM rules for SMM, and standard rules for others
  const categorizedTasks = useMemo(() => {
    const today = activeDateRange?.from ? new Date(activeDateRange.from) : new Date()
    today.setHours(0, 0, 0, 0)
    
    const todayList: any[] = []
    const pendingList: any[] = []
    const upcomingList: any[] = []

    const completedList: any[] = []

    filteredByDateTasks.forEach(t => {
      const isCompleted = t.status === 'completed' || t.status === 'Approved' || t.status?.toLowerCase() === 'completed'
      
      const deadlineDate = t.dueDate ? parseLocalDate(t.dueDate) : new Date(0)

      if (isCompleted) {
        // Since we don't have completion date, we approximate by showing tasks due on the selected date
        if (deadlineDate.getTime() === today.getTime()) {
          completedList.push(t)
        }
        return
      }

      const hasApplicableRemark = t.originalTask?.remark && typeof t.originalTask.remark === 'string' && t.originalTask.remark.trim() !== '' && (
        !t.originalTask.remarkStage || 
        isStageSubsequentOrEqual(t.stage, t.originalTask.remarkStage, t.originalTask.postReel)
      );
      const isClientIssue = (t.status && t.status.toLowerCase() === 'client issue') || 
                            (hasApplicableRemark && t.originalTask.remark.startsWith('[CLIENT ISSUE]'));

      if (isClientIssue) {
        pendingList.push(t);
        return; // Don't process further, it belongs exclusively in pending
      }

      if (t.sourceType.startsWith('smm-')) {
        // SMM Task Categorization
        if (t.sourceType === 'smm-other') {
          // Check Today / Upcoming
          if (deadlineDate <= today) {
            todayList.push(t)
          } else {
            upcomingList.push(t)
          }
          // Check Pending
          if (t.status === 'Pending' || t.status === 'Ready for Review') {
            pendingList.push(t)
          }
        } else if (t.sourceType === 'smm-creative') {
          // Check Today / Upcoming
          if (deadlineDate <= today) {
            todayList.push(t)
          } else {
            upcomingList.push(t)
          }
        } else if (t.sourceType === 'smm-followup') {
          if (deadlineDate <= today) {
            todayList.push(t)
          } else {
            upcomingList.push(t)
          }
        }
      } else {
        // General / HR / Development Tasks Categorization
        if (!t.dueDate) {
          if (t.frequency === 'daily') {
            todayList.push(t)
          } else {
            pendingList.push(t)
          }
        } else {
          // Compare parsed Date objects instead of strings!
          if (deadlineDate.getTime() === today.getTime() || (t.frequency === 'daily' && deadlineDate <= today)) {
            todayList.push(t)
          } else if (deadlineDate < today) {
            // Overdue tasks are shown under Today's Tasks
            todayList.push(t)
            // And also under Pending Tasks
            pendingList.push(t)
          } else {
            upcomingList.push(t)
          }
        }
      }
    })

    const sortByDate = (list: any[]) => {
      return [...list].sort((a, b) => {
        const dA = a.dueDate ? parseLocalDate(a.dueDate).getTime() : 0;
        const dB = b.dueDate ? parseLocalDate(b.dueDate).getTime() : 0;
        return dA - dB;
      });
    };

    return { 
      today: sortByDate(todayList), 
      pending: sortByDate(pendingList), 
      upcoming: sortByDate(upcomingList), 
      completed: sortByDate(completedList) 
    }
  }, [filteredByDateTasks, targetDate])

  // Group task lists by Department for nice UI sectioning
  const groupTasksByDepartment = (taskList: any[]) => {
    const grouped: Record<string, any[]> = {}
    taskList.forEach(t => {
      const dept = t.department || 'General Tasks'
      if (!grouped[dept]) {
        grouped[dept] = []
      }
      grouped[dept].push(t)
    })
    return grouped
  }

  const getPriorityColor = (prio: string) => {
    switch (prio?.toLowerCase()) {
      case 'urgent': return 'bg-rose-50 text-rose-700 border-rose-200'
      case 'high': return 'bg-amber-50 text-amber-700 border-amber-200'
      case 'medium': return 'bg-blue-50 text-blue-700 border-blue-200'
      default: return 'bg-slate-50 text-slate-600 border-slate-200'
    }
  }

  const getDeptIcon = (dept: string) => {
    switch (dept) {
      case 'Development': return <Code className="w-4 h-4 text-indigo-600" />
      case 'Social Media Management': return <Megaphone className="w-4 h-4 text-pink-600" />
      case 'Digital Marketing': return <Megaphone className="w-4 h-4 text-teal-600" />
      case 'HR Tasks': return <UserCheck className="w-4 h-4 text-emerald-600" />
      default: return <Briefcase className="w-4 h-4 text-slate-600" />
    }
  }

  return (
    <div className={isEmbedded ? "w-full" : "min-h-screen bg-slate-50 flex flex-col"}>
      {!isEmbedded && (
        <PageHeader 
          title="My Tasks"
          description="View and manage all your assigned tasks across departments"
        >
          <Button className="bg-brand-teal hover:bg-brand-teal/90 text-white font-bold h-9">
            <Plus className="w-4 h-4 mr-2" />
            New Task
          </Button>
        </PageHeader>
      )}

        <div className={isEmbedded ? "flex-1" : "flex-1 p-6"}>
          <div className="max-w-[1400px] mx-auto space-y-6 mb-6">

            {/* KPI Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex justify-between items-center">
                <div>
                  <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Total Active Tasks</div>
                  <div className="text-2xl font-black text-slate-800">{kpiData.total}</div>
                </div>
                <div className="h-10 w-10 rounded-xl bg-slate-50 flex items-center justify-center text-slate-500">
                  <Briefcase className="w-5 h-5" />
                </div>
              </div>
              <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex justify-between items-center">
                <div>
                  <div className="text-[10px] font-bold text-blue-500 uppercase tracking-wider mb-1">In Progress</div>
                  <div className="text-2xl font-black text-slate-800">{kpiData.inProgress}</div>
                </div>
                <div className="h-10 w-10 rounded-xl bg-blue-50 flex items-center justify-center text-blue-500">
                  <Clock className="w-5 h-5" />
                </div>
              </div>
              <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex justify-between items-center">
                <div>
                  <div className="text-[10px] font-bold text-amber-500 uppercase tracking-wider mb-1">Pending Review</div>
                  <div className="text-2xl font-black text-slate-800">{kpiData.pendingReview}</div>
                </div>
                <div className="h-10 w-10 rounded-xl bg-amber-50 flex items-center justify-center text-amber-500">
                  <CheckCircle2 className="w-5 h-5" />
                </div>
              </div>
              <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex justify-between items-center">
                <div>
                  <div className="text-[10px] font-bold text-rose-500 uppercase tracking-wider mb-1">Overdue</div>
                  <div className="text-2xl font-black text-slate-800">{kpiData.overdue}</div>
                </div>
                <div className="h-10 w-10 rounded-xl bg-rose-50 flex items-center justify-center text-rose-500">
                  <AlertCircle className="w-5 h-5" />
                </div>
              </div>
            </div>

            {/* Sales KPIs */}
            {isSalesDept && salesKpiData && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
                <div className="bg-white border border-brand-teal/20 rounded-xl p-4 shadow-sm flex justify-between items-center">
                  <div>
                    <div className="text-[10px] font-bold text-brand-teal uppercase tracking-wider mb-1">Today's Leads</div>
                    <div className="text-2xl font-black text-slate-800">{salesKpiData.totalLeadsToday}</div>
                  </div>
                  <div className="h-10 w-10 rounded-xl bg-brand-teal/10 flex items-center justify-center text-brand-teal">
                    <UserCheck className="w-5 h-5" />
                  </div>
                </div>
                <div className="bg-white border border-rose-200 rounded-xl p-4 shadow-sm flex justify-between items-center">
                  <div>
                    <div className="text-[10px] font-bold text-rose-500 uppercase tracking-wider mb-1">Hot Leads</div>
                    <div className="text-2xl font-black text-slate-800">{salesKpiData.hotLeads}</div>
                  </div>
                  <div className="h-10 w-10 rounded-xl bg-rose-50 flex items-center justify-center text-rose-500">
                    <AlertCircle className="w-5 h-5" />
                  </div>
                </div>
                <div className="bg-white border border-emerald-200 rounded-xl p-4 shadow-sm flex justify-between items-center">
                  <div>
                    <div className="text-[10px] font-bold text-emerald-500 uppercase tracking-wider mb-1">Converted Today</div>
                    <div className="text-2xl font-black text-slate-800">{salesKpiData.convertedLeads}</div>
                  </div>
                  <div className="h-10 w-10 rounded-xl bg-emerald-50 flex items-center justify-center text-emerald-500">
                    <CheckCircle2 className="w-5 h-5" />
                  </div>
                </div>
                <div className="bg-white border border-indigo-200 rounded-xl p-4 shadow-sm flex justify-between items-center">
                  <div>
                    <div className="text-[10px] font-bold text-indigo-500 uppercase tracking-wider mb-1">Followups Today</div>
                    <div className="text-2xl font-black text-slate-800">{salesKpiData.followupsToday}</div>
                  </div>
                  <div className="h-10 w-10 rounded-xl bg-indigo-50 flex items-center justify-center text-indigo-500">
                    <CalendarIcon className="w-5 h-5" />
                  </div>
                </div>
              </div>
            )}

          </div>

          {loading ? (
            <div className="flex items-center justify-center py-24">
              <Loader2 className="w-8 h-8 animate-spin text-brand-teal" />
            </div>
          ) : (
            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
              <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6">
                <TabsList className="inline-flex items-center gap-1 w-max bg-slate-100/70 p-1 rounded-xl shadow-inner border border-slate-200/60 h-auto justify-start shrink-0 mb-0">
                <TabsTrigger value="today" className="data-[state=active]:bg-white data-[state=active]:text-brand-teal data-[state=active]:shadow-sm data-[state=active]:border-slate-200/50 px-6 py-2 rounded-lg text-sm font-bold transition-all whitespace-nowrap hover:bg-slate-200/50 border border-transparent h-auto">
                  {targetDate ? 'Assigned Tasks' : "Today's Tasks"} ({categorizedTasks.today.length})
                </TabsTrigger>
                <TabsTrigger value="pending" className="data-[state=active]:bg-white data-[state=active]:text-brand-teal data-[state=active]:shadow-sm data-[state=active]:border-slate-200/50 px-6 py-2 rounded-lg text-sm font-bold transition-all whitespace-nowrap hover:bg-slate-200/50 border border-transparent h-auto">
                  Pending Tasks ({categorizedTasks.pending.length})
                </TabsTrigger>
                <TabsTrigger value="upcoming" className="data-[state=active]:bg-white data-[state=active]:text-brand-teal data-[state=active]:shadow-sm data-[state=active]:border-slate-200/50 px-6 py-2 rounded-lg text-sm font-bold transition-all whitespace-nowrap hover:bg-slate-200/50 border border-transparent h-auto">
                  Upcoming Tasks ({categorizedTasks.upcoming.length})
                </TabsTrigger>
                <TabsTrigger value="completed" className="data-[state=active]:bg-white data-[state=active]:text-brand-teal data-[state=active]:shadow-sm data-[state=active]:border-slate-200/50 px-6 py-2 rounded-lg text-sm font-bold transition-all whitespace-nowrap hover:bg-slate-200/50 border border-transparent h-auto">
                  Completed Tasks ({categorizedTasks.completed.length})
                </TabsTrigger>
                </TabsList>
                
                <div className="flex flex-wrap items-center gap-4">
                  {!isEmbedded && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-slate-500 hidden sm:inline-block">Filter by Date:</span>
                      <Popover>
                        <PopoverTrigger asChild>
                          <Button
                            variant="outline"
                            className={`h-9 px-3 text-xs bg-white border border-slate-200 rounded-lg outline-none text-slate-700 font-semibold shadow-sm hover:bg-slate-50 transition-colors justify-start text-left min-w-[200px]`}
                          >
                            <CalendarIcon className="mr-2 h-3.5 w-3.5 text-slate-400" />
                            {activeDateRange?.from ? (
                              activeDateRange.to ? (
                                <>
                                  {format(activeDateRange.from, "dd/MM/yyyy")} -{" "}
                                  {format(activeDateRange.to, "dd/MM/yyyy")}
                                </>
                              ) : (
                                format(activeDateRange.from, "dd/MM/yyyy")
                              )
                            ) : (
                              <span>Pick a date range</span>
                            )}
                          </Button>
                        </PopoverTrigger>
                        <PopoverContent className="w-auto p-0 border-slate-200 rounded-xl shadow-xl" align="start">
                          <DayCalendar
                            initialFocus
                            mode="range"
                            defaultMonth={activeDateRange?.from}
                            selected={activeDateRange}
                            onSelect={setActiveDateRange as any}
                            numberOfMonths={2}
                          />
                        </PopoverContent>
                      </Popover>
                      
                      {activeDateRange && (
                        <Button 
                          variant="ghost" 
                          onClick={() => setActiveDateRange(undefined)}
                          className="h-9 px-3 text-xs text-slate-500 hover:text-slate-800 hover:bg-slate-100 font-semibold rounded-lg transition-colors"
                        >
                          Clear
                        </Button>
                      )}
                    </div>
                  )}

                  {!isEmbedded && currentUser && ['admin', 'superadmin', 'head'].includes(currentUser.role?.toLowerCase()) && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-slate-500 hidden sm:inline-block">Employee:</span>
                      <SearchableSelect
                        options={[
                          { value: "all", label: "My Tasks Only" },
                          ...employees.map(emp => ({
                            value: emp.id,
                            label: emp.name || `${emp.firstName} ${emp.lastName}`
                          }))
                        ]}
                        value={selectedEmployeeId}
                        onValueChange={setSelectedEmployeeId}
                        placeholder="All Employees"
                        triggerClassName="h-9 w-[200px]"
                      />
                    </div>
                  )}
                </div>
              </div>

              {['today', 'pending', 'upcoming', 'completed'].map(tabKey => {
                const currentList = categorizedTasks[tabKey as 'today' | 'pending' | 'upcoming' | 'completed']
                const grouped = groupTasksByDepartment(currentList)
                
                return (
                  <TabsContent key={tabKey} value={tabKey} className="space-y-6 mt-0">
                    {currentList.length === 0 ? (
                      <Card className="border border-slate-200 shadow-none">
                        <CardContent className="flex flex-col items-center justify-center py-16">
                          <AlertCircle className="w-10 h-10 text-slate-400 mb-3" />
                          <p className="text-sm font-medium text-slate-500">No tasks in this section.</p>
                        </CardContent>
                      </Card>
                    ) : (
                      Object.keys(grouped).map(deptName => (
                        <div key={deptName} className="space-y-3">
                          <div className="flex items-center gap-2 px-1">
                            {getDeptIcon(deptName)}
                            <h2 className="text-sm font-bold text-slate-700 tracking-wide uppercase">{deptName}</h2>
                            <span className="text-[10px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full font-bold">
                              {grouped[deptName].length}
                            </span>
                          </div>
                          
                          <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm">
                            {deptName === 'Social Media Management' ? (
                              <table className="w-full text-left border-collapse">
                                <thead>
                                  <tr className="bg-slate-50 text-[10px] uppercase font-bold text-slate-500 tracking-wider border-b border-slate-200">
                                    <th className="p-4 w-[120px]">Due Date</th>
                                    <th className="p-4 w-[250px]">Client / Project</th>
                                    <th className="p-4 w-[120px]">Stage</th>
                                    <th className="p-4">Task Details</th>
                                    <th className="p-4 w-[200px]">Remark</th>
                                    <th className="p-4 w-[120px] text-right">Actions</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 text-xs">
                                  {grouped[deptName].map(task => {
                                    const todayStr = new Date().toISOString().split('T')[0]
                                    const isOverdue = task.dueDate && task.dueDate < todayStr
                                    const isCustomTask = task.description === "Custom task created from Punch-In" || task.originalTask?.description === "Custom task created from Punch-In"
                                    return (
                                      <tr 
                                        key={task.id} 
                                        onClick={() => {
                                          const url = getRedirectUrl(task);
                                          if (url) router.push(url);
                                        }}
                                        className={`hover:bg-slate-50/50 transition-colors group ${getRedirectUrl(task) ? 'cursor-pointer' : ''}`}
                                      >
                                        <td className="p-4">
                                          <span className={`font-bold text-[10px] rounded px-2.5 py-1 ${isOverdue ? 'bg-red-800 text-white' : 'bg-slate-100 text-slate-700'}`}>
                                            {task.dueDate ? format(new Date(task.dueDate), 'dd/MM/yyyy') : '-'}
                                          </span>
                                        </td>
                                        <td className="p-4 font-bold text-slate-800">
                                          {task.clientDisplayName || '-'}
                                        </td>
                                        <td className="p-4">
                                          <Badge className={`border font-black text-[9px] uppercase shadow-none ${
                                            task.stage === 'Brand Person' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
                                            task.stage === 'Caption' ? 'bg-teal-50 text-teal-700 border-teal-200' :
                                            task.stage === 'Script' ? 'bg-cyan-50 text-cyan-700 border-cyan-200' :
                                            'bg-slate-50 text-slate-700 border-slate-200'
                                          }`}>
                                            {task.stage || '-'}
                                          </Badge>
                                        </td>
                                        <td className="p-4" title={task.title}>
                                          <div className="font-bold text-slate-800 truncate max-w-[400px]">{task.title}</div>
                                          <div className="text-[10px] text-slate-400 mt-0.5">
                                            Assigned by: {task.originalTask?.assignerName || 'TL Graphics'} {task.originalTask?.assigneeName ? `Assigned to: ${task.originalTask.assigneeName}` : `Assigned to: ${currentUser?.name || 'User'}`}
                                          </div>
                                        </td>
                                        <td className="p-4 text-slate-500 italic max-w-[200px] truncate" title={task.originalTask?.remark || '-'}>
                                          {task.originalTask?.remark || '-'}
                                        </td>
                                        <td className="p-4 text-right">
                                          <div className="flex items-center justify-end gap-1.5">
                                            {task.status !== 'completed' && task.status !== 'Approved' && task.status?.toLowerCase() !== 'completed' ? (
                                              isCustomTask && (
                                                <Button
                                                  variant="ghost"
                                                  size="icon"
                                                  className="h-8 w-8 text-emerald-500 hover:text-emerald-600 hover:bg-emerald-50"
                                                  onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleMarkComplete(task);
                                                  }}
                                                  title="Mark Complete"
                                                >
                                                  <CheckCircle2 className="w-4 h-4" />
                                                </Button>
                                              )
                                            ) : (
                                              <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">
                                                Completed
                                              </Badge>
                                            )}
                                            {getRedirectUrl(task) && (
                                              <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8 text-blue-500 hover:text-blue-600 hover:bg-blue-50"
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  const url = getRedirectUrl(task);
                                                  if (url) router.push(url);
                                                }}
                                                title="Go to Task Section"
                                              >
                                                <ExternalLink className="w-4 h-4" />
                                              </Button>
                                            )}
                                          </div>
                                        </td>
                                      </tr>
                                    )
                                  })}
                                </tbody>
                              </table>
                            ) : (
                              <table className="w-full text-left border-collapse">
                                <thead>
                                  <tr className="bg-slate-50 text-[10px] uppercase font-bold text-slate-500 tracking-wider border-b border-slate-200">
                                    <th className="p-4 w-[120px]">Due Date</th>
                                    <th className="p-4 w-[250px]">Project</th>
                                    <th className="p-4">Task Details</th>
                                    <th className="p-4 w-[100px]">Priority</th>
                                    <th className="p-4 w-[120px] text-right">Actions</th>
                                  </tr>
                                </thead>
                                  <tbody className="divide-y divide-slate-100 text-xs">
                                    {grouped[deptName].map(task => {
                                      const todayStr = new Date().toISOString().split('T')[0]
                                      const isOverdue = task.dueDate && task.dueDate < todayStr
                                      const isCustomTask = task.description === "Custom task created from Punch-In" || task.originalTask?.description === "Custom task created from Punch-In"
                                        return (
                                          <tr 
                                            key={task.id} 
                                            onClick={() => {
                                              if (!isCustomTask) {
                                                const url = getRedirectUrl(task);
                                                if (url) router.push(url);
                                              }
                                            }}
                                            className={`hover:bg-slate-50/50 transition-colors group ${!isCustomTask && getRedirectUrl(task) ? 'cursor-pointer' : ''}`}
                                          >
                                            <td className="p-4 font-semibold text-slate-700">
                                              <div className={`flex items-center gap-1.5 ${isOverdue ? 'text-red-600 font-bold' : ''}`}>
                                                {isOverdue ? <AlertCircle className="w-3.5 h-3.5 text-red-600" /> : <CalendarIcon className="w-3.5 h-3.5 text-slate-400" />}
                                                <span className={isOverdue ? 'bg-red-100 px-2 py-0.5 rounded text-[10px]' : ''}>
                                                  {task.dueDate ? format(new Date(task.dueDate), 'dd/MM/yyyy') : 'No Date'}
                                                </span>
                                              </div>
                                            </td>
                                        <td className="p-4 font-bold text-slate-800">
                                          {task.projectName || '-'}
                                        </td>
                                        <td className="p-4" title={`${task.title}${task.description ? '\n' + task.description : ''}`}>
                                          <div className="font-semibold text-slate-700 truncate max-w-[400px]">{task.title}</div>
                                          {task.description && <div className="text-[10px] text-slate-400 mt-0.5 truncate max-w-[400px]">{task.description}</div>}
                                        </td>
                                        <td className="p-4">
                                          <Badge className={`${getPriorityColor(task.priority)} border font-bold text-[9px] uppercase shadow-none`}>
                                            {task.priority}
                                          </Badge>
                                        </td>
                                        <td className="p-4 text-right">
                                          <div className="flex items-center justify-end gap-1.5">
                                            {task.status !== 'completed' && task.status !== 'Approved' && task.status?.toLowerCase() !== 'completed' ? (
                                              isCustomTask && (
                                                <Button
                                                  variant="ghost"
                                                  size="icon"
                                                  className="h-8 w-8 text-emerald-500 hover:text-emerald-600 hover:bg-emerald-50"
                                                  onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleMarkComplete(task);
                                                  }}
                                                  title="Mark Complete"
                                                >
                                                  <CheckCircle2 className="w-4 h-4" />
                                                </Button>
                                              )
                                            ) : (
                                              <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">
                                                Completed
                                              </Badge>
                                            )}
                                            {getRedirectUrl(task) && (
                                              <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8 text-blue-500 hover:text-blue-600 hover:bg-blue-50"
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  const url = getRedirectUrl(task);
                                                  if (url) router.push(url);
                                                }}
                                                title="Go to Task Section"
                                              >
                                                <ExternalLink className="w-4 h-4" />
                                              </Button>
                                            )}
                                          </div>
                                        </td>
                                    </tr>
                                      )
                                    })}
                                </tbody>
                              </table>
                            )}
                          </div>
                        </div>
                      ))
                    )}
                  </TabsContent>
                )
              })}
            </Tabs>
          )}
        </div>
      </div>
  )
}
