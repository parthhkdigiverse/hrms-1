'use client'

import { useState, useEffect, useMemo } from 'react'
import { DataTable } from '@/components/hrms/data-table'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Calendar, User, Users, Briefcase, CheckCircle2, XCircle, Clock, Search, Filter, Star } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { useUser } from '@/hooks/useUser'
import { API_URL } from '@/lib/config'
import { toast } from 'sonner'
import { MyTasksView } from '@/components/hrms/MyTasksView'
import { useRouter } from 'next/navigation'
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar as CalendarComponent } from "@/components/ui/calendar"
import { format, eachDayOfInterval } from "date-fns"
import { DateRange } from "react-day-picker"
import { usePermissions } from '@/hooks/usePermissions'
import { Loader2, MessageSquare, History, AlertCircle } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ActivityLogDialog } from '@/components/common/ActivityLogDialog'

interface DailyProgressViewProps {
  defaultDepartment?: string;
}

export function DailyProgressView({ defaultDepartment }: DailyProgressViewProps) {
  const { data, refreshItem } = useApi()
  const { user } = useUser()
  const router = useRouter()
  const { checkPermission, isAdmin: isUserAdmin, loading: permissionsLoading } = usePermissions()

  const isHRRoleOrDept = user?.role?.toLowerCase() === 'hr' || user?.designation?.toLowerCase()?.includes('hr') || user?.department?.toLowerCase()?.includes('hr')
  const canViewDailyProgress = isUserAdmin || isHRRoleOrDept || checkPermission('daily-progress', 'canView') || ['Employee', 'Team Leader', 'Manager', 'Social Media Manager'].includes(user?.role || '')
  const canEditDailyProgress = isUserAdmin || isHRRoleOrDept || checkPermission('daily-progress', 'canEdit')

  const employees = data?.employees || []
  const allReports = (data as any)?.employeeDailyReports || []
  const attendanceRecords = (data as any)?.attendanceRecords || []
  const leaveRequests = (data as any)?.leaveRequests || []
  
  const [dateRange, setDateRange] = useState<DateRange | undefined>({
    from: new Date(),
    to: new Date()
  })
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [noteRecord, setNoteRecord] = useState<any>(null)
  const [noteText, setNoteText] = useState('')
  const [logsOpen, setLogsOpen] = useState(false)
  const [reportLogs, setReportLogs] = useState<any[]>([])
  const [isLoadingLogs, setIsLoadingLogs] = useState(false)
  const [logFilter, setLogFilter] = useState<{reportId?: string, employeeName?: string}>({})
  const [activeDeptTab, setActiveDeptTab] = useState<string>(defaultDepartment || '')
  const [activeRoleTab, setActiveRoleTab] = useState<'Team Leaders' | 'Employees'>('Team Leaders')
  const [selectedStatusFilter, setSelectedStatusFilter] = useState('all')
  const [tlViewMode, setTlViewMode] = useState<'my' | 'team'>('team')
  
  const [ratingDialogOpen, setRatingDialogOpen] = useState(false)
  const [ratingFilter, setRatingFilter] = useState<'yesterday' | 'this_week' | 'all'>('all')
  const [ratingsModalOpen, setRatingsModalOpen] = useState(false)
  const [ratingDateFilter, setRatingDateFilter] = useState<'yesterday' | 'this_week' | 'all'>('all')

  const [verifyRecord, setVerifyRecord] = useState<any>(null)
  const [verifyNote, setVerifyNote] = useState('')
  const [verifyRating, setVerifyRating] = useState('')
  const [pendingTasks, setPendingTasks] = useState<any[]>([])
  const [isLoadingPendingTasks, setIsLoadingPendingTasks] = useState(false)
  const [approveRecord, setApproveRecord] = useState<any>(null)
  const [approveRating, setApproveRating] = useState<number | ''>('')

  const availableDepartments = useMemo(() => {
    if (!employees || employees.length === 0) return []
    const depts = new Set(employees.map((e: any) => e.department).filter(Boolean))
    return Array.from(depts).sort() as string[]
  }, [employees])

  const isAdmin = user?.role?.toLowerCase() === 'admin'
  const isTeamLeader = (user?.designation?.toLowerCase() === 'team leader' || user?.designation?.toLowerCase() === 'head')
  const isHRUser = user?.role?.toLowerCase() === 'hr' || user?.designation?.toLowerCase()?.includes('hr') || user?.department?.toLowerCase()?.includes('hr')

  useEffect(() => {
    if (isTeamLeader && user?.department && !activeDeptTab) {
      setActiveDeptTab(user.department)
    }
  }, [isTeamLeader, user, activeDeptTab])

  useEffect(() => {
    if (verifyRecord?.employeeId && verifyRecord?.date) {
      setIsLoadingPendingTasks(true);
      
      const employeeDept = verifyRecord.department?.toLowerCase() || '';
      const isSmmDept = ['smm', 'creative'].includes(employeeDept);
      const isDmDept = ['dm', 'digital marketing'].includes(employeeDept);
      
      if (isSmmDept) {
        Promise.all([
          fetch(`${API_URL}/content-calendar/all`).then(res => res.json()),
          fetch(`${API_URL}/other-work/all`).then(res => res.json()),
          fetch(`${API_URL}/projects`).then(res => res.json()),
          fetch(`${API_URL}/clients`).then(res => res.json())
        ])
        .then(([ccData, owData, projectsData, clientsData]) => {
          let smmTasks: any[] = [];
          const targetId = verifyRecord.employeeId;
          const targetDate = verifyRecord.date;
          
          const projects = Array.isArray(projectsData) ? projectsData : [];
          const clients = Array.isArray(clientsData) ? clientsData : [];
          
          if (Array.isArray(ccData)) {
            ccData.forEach(entry => {
              const project = projects.find((p: any) => p.id === entry.projectId) || {};
              const client = clients.find((c: any) => c.id === (entry.clientId || project.clientId)) || {};
              
              const addIfMatches = (stage: string, date: string, assigneeId: string, isCompleted: boolean) => {
                if (!date || date > targetDate || isCompleted) return;
                if (assigneeId !== targetId) return;
                smmTasks.push({
                  id: `${entry.id}-${stage}`,
                  title: `${entry.concept || entry.topic || 'Content Task'} - ${stage}`,
                  department: 'SMM'
                });
              };
              
              const isGraphicPost = entry.postReel === 'Post';
              const isStory = entry.postReel === 'Story';
              
              const scriptAssignee = entry.assignedScriptwriterId || project.assignedScriptwriterId || client.assignedScriptwriterId;
              addIfMatches('Script', entry.scriptDate, scriptAssignee, !!entry.scriptLink || isGraphicPost || isStory);
              
              const shootDate = entry.shootDate || entry.scriptDate;
              const shootLink = entry.shootLink || entry.shootingLink;
              const shootAssignee = entry.assignedShooterId || project.assignedShooterId || client.assignedShooterId;
              addIfMatches('Shoot', shootDate, shootAssignee, !!shootLink || isGraphicPost || isStory);
              
              if (!isStory && entry.assignedBrandPersonIds && (!shootLink || shootLink === '-')) {
                const bpIdsRaw = entry.assignedBrandPersonIds;
                const bpIds = Array.isArray(bpIdsRaw) ? bpIdsRaw : (typeof bpIdsRaw === 'string' ? bpIdsRaw.split(',').map((id: string) => id.trim()).filter(Boolean) : []);
                if (bpIds.includes(targetId)) {
                  const taskDeadline = entry.shootDate || entry.postingDate || (entry.monthYear ? `${entry.monthYear}-28` : new Date().toISOString().split('T')[0]);
                  if (taskDeadline && taskDeadline <= targetDate) {
                    smmTasks.push({
                      id: `${entry.id}-BrandPerson`,
                      title: `${entry.concept || entry.topic || 'Content Task'} - Brand Person`,
                      department: 'SMM'
                    });
                  }
                }
              }

              const editAssignee = isGraphicPost 
                ? (entry.assignedPostDesignerId || project.assignedPostDesignerId || client.assignedPostDesignerId)
                : (entry.assignedReelEditorId || project.assignedReelEditorId || client.assignedReelEditorId);
              addIfMatches('Editing', entry.editingStart, editAssignee, isGraphicPost ? !!entry.finalPostLink : !!entry.finalReelLink);
              
              const captionAssignee = entry.assignedCaptionWriterId || project.assignedCaptionWriterId || client.assignedCaptionWriterId;
              addIfMatches('Caption', entry.captionDate || entry.editingStart, captionAssignee, !!entry.caption || isStory);
              
              if (!isGraphicPost && !isStory) {
                const thumbAssignee = entry.assignedThumbnailDesignerId || project.assignedThumbnailDesignerId || client.assignedThumbnailDesignerId;
                addIfMatches('Thumbnail', entry.thumbnailDate || entry.editingStart, thumbAssignee, !!entry.thumbnailLink);
              }
              
              const approverAssignee = entry.assignedApproverId || project.assignedApproverId || client.assignedApproverId;
              addIfMatches('Approval', entry.approval, approverAssignee, entry.isApproved === 'Yes');
              
              if (!isStory) {
                const posterAssignee = entry.assignedPosterId || project.assignedPosterId || client.assignedPosterId;
                addIfMatches('Posting', entry.postingDate, posterAssignee, !!entry.postingLinkOfIg);
              }
            });
          }
          
          if (Array.isArray(projects)) {
            projects.forEach((p: any) => {
              if (p.department?.toLowerCase() === 'digital marketing') return; // Skip DM projects for SMM employees
              
              const client = clients.find((c: any) => c.id === p.clientId) || {};
              const followUpAssignee = p.assignedFollowUpId || client.assignedFollowUpId;
              
              if (followUpAssignee === targetId && p.nextFollowupDate) {
                const nextDate = p.nextFollowupDate.split("T")[0].split(" ")[0];
                if (nextDate <= targetDate) {
                  smmTasks.push({
                    id: `${p.id}-FollowUp`,
                    title: `${client.companyName || p.title || 'Client'} - Follow-up`,
                    department: 'SMM'
                  });
                }
              }
            });
          }
          
          if (Array.isArray(owData)) {
            owData.forEach(ow => {
              if (ow.assigneeId === targetId && ow.deadline && ow.deadline <= targetDate) {
                if (ow.status !== 'Approved' && ow.status !== 'Completed') {
                  smmTasks.push({
                    id: ow.id,
                    title: ow.title || 'Other Work',
                    department: 'SMM'
                  });
                }
              }
            });
          }
          setPendingTasks(smmTasks);
        })
        .catch(err => console.error("Error fetching SMM pending tasks:", err))
        .finally(() => setIsLoadingPendingTasks(false));
      } else if (isDmDept) {
        Promise.all([
          fetch(`${API_URL}/marketing/project-remarks`).then(res => res.json()),
          fetch(`${API_URL}/projects`).then(res => res.json()),
          fetch(`${API_URL}/clients`).then(res => res.json())
        ])
        .then(([prData, projectsData, clientsData]) => {
          let dmTasks: any[] = [];
          const targetId = verifyRecord.employeeId;
          const targetDate = verifyRecord.date;
          
          const projects = Array.isArray(projectsData) ? projectsData : [];
          const clients = Array.isArray(clientsData) ? clientsData : [];
          const projectRemarks = Array.isArray(prData) ? prData : [];
          
          const normalizeDate = (d: string) => d ? d.split(" ")[0].split("T")[0] : "";
          
          clients.forEach(client => {
            const clientProjects = projects.filter((p: any) => p.clientId === client.id && p.department?.toLowerCase() === "digital marketing" && p.status?.toLowerCase() === "active");
            const proj = clientProjects[0];
            
            if (proj) {
              const hasDataFill = allReports.some((r: any) => r.clientId === client.id && normalizeDate(r.date) === targetDate);
              const hasMetrics = projectRemarks.some((r: any) => r.projectId === proj.id && normalizeDate(r.date) === targetDate);
              
              let dayTasks = [
                { id: "data_fill", name: "Data Fill", assigneeId: proj.assignedEmployeeId, date: targetDate },
                { id: "revenue", name: "Revenue", assigneeId: proj.revenueAssigneeId, date: targetDate },
                { id: "follower", name: "Follower", assigneeId: proj.followerAssigneeId, date: targetDate },
                { id: "user_remark", name: "User Remark", assigneeId: proj.userRemarkAssigneeId, date: targetDate },
                { id: "client_remark", name: "Client Remark", assigneeId: proj.clientRemarkAssigneeId, date: targetDate },
              ].filter(t => t.assigneeId === targetId);
              
              if (hasDataFill) {
                dayTasks = dayTasks.filter(t => t.id !== "data_fill");
              }
              if (hasMetrics) {
                dayTasks = dayTasks.filter(t => !["revenue", "follower", "user_remark", "client_remark"].includes(t.id));
              }
              
              dayTasks.forEach(t => {
                dmTasks.push({
                  id: `${proj.id}-${t.id}`,
                  title: `${client.companyName || 'Client'} - ${t.name}`,
                  department: 'Digital Marketing'
                });
              });
            }
          });
          
          setPendingTasks(dmTasks);
        })
        .catch(err => console.error("Error fetching DM pending tasks:", err))
        .finally(() => setIsLoadingPendingTasks(false));
      } else {
        fetch(`${API_URL}/wm-tasks`)
          .then(res => res.json())
          .then(data => {
            if (Array.isArray(data)) {
              const tasks = data.filter((t: any) => {
                // Must be assigned to this employee
                if (t.assignedToId !== verifyRecord.employeeId) return false;

                const taskDate = t.dueDate || t.postingDate;
                const isDev = t.department?.toLowerCase() === 'development' || employeeDept === 'development';
                
                if (isDev) {
                  if (!taskDate || taskDate > verifyRecord.date) return false;
                  return true;
                } else {
                  if (t.status === 'completed' || t.status === 'approved') return false;
                  if (!taskDate || taskDate > verifyRecord.date) return false;
                  return true;
                }
              });
              setPendingTasks(tasks);
            }
          })
          .catch(err => console.error("Error fetching pending tasks:", err))
          .finally(() => setIsLoadingPendingTasks(false))
      }
    } else {
      setPendingTasks([])
    }
  }, [verifyRecord?.employeeId, verifyRecord?.date])

  const ratingData = useMemo(() => {
     if (!isAdmin) return []
     const today = new Date()
     today.setHours(0,0,0,0)
     const yesterday = new Date(today)
     yesterday.setDate(yesterday.getDate() - 1)
     
     const startOfThisWeek = new Date(today)
     startOfThisWeek.setDate(today.getDate() - today.getDay()) // Sunday as start

     let filteredReports = allReports.filter((r: any) => r.rating)

     if (ratingFilter === 'yesterday') {
        const yesterdayStr = format(yesterday, "yyyy-MM-dd")
        filteredReports = filteredReports.filter((r: any) => r.date === yesterdayStr)
     } else if (ratingFilter === 'this_week') {
        filteredReports = filteredReports.filter((r: any) => {
           const reportDate = new Date(r.date)
           reportDate.setHours(0,0,0,0)
           return reportDate >= startOfThisWeek && reportDate <= today
        })
     }

     const empMap = new Map<string, { employeeName: string, department: string, totalScore: number, count: number }>()
     
     filteredReports.forEach((r: any) => {
        if (!empMap.has(r.employeeId)) {
           empMap.set(r.employeeId, {
              employeeName: r.employeeName || employees.find((e:any)=>e.id===r.employeeId)?.name || 'Unknown',
              department: r.department || employees.find((e:any)=>e.id===r.employeeId)?.department || '',
              totalScore: 0,
              count: 0
           })
        }
        const data = empMap.get(r.employeeId)!
        data.totalScore += Number(r.rating)
        data.count += 1
     })

     const result = Array.from(empMap.values()).map(d => ({
        employeeName: d.employeeName,
        department: d.department,
        avgRating: (d.totalScore / d.count).toFixed(1),
        count: d.count
     }))
     
     return result.sort((a, b) => Number(b.avgRating) - Number(a.avgRating))
  }, [allReports, ratingFilter, isAdmin, employees])

  // Combine employees with their report status for the selected date
  const displayData = useMemo(() => {
    let filteredEmployees = employees.filter((e: any) => e.status?.trim()?.toLowerCase() === 'active')
    
    if (!isAdmin && !isTeamLeader && !isHRUser) {
       filteredEmployees = filteredEmployees.filter((e: any) => e.id === user?.id)
    } else {
       const deptToFilter = (isTeamLeader && !isAdmin && !isHRUser) ? user?.department : (isAdmin || isHRUser ? '' : activeDeptTab)
       if (deptToFilter) {
         filteredEmployees = filteredEmployees.filter((e: any) => e.department?.toLowerCase() === deptToFilter.toLowerCase())
       }

        if (isAdmin || isHRUser) {
          if (activeRoleTab === 'Team Leaders') {
            filteredEmployees = filteredEmployees.filter((e: any) => {
               const rStr = (e.role || '').toLowerCase();
               const dStr = (e.designation || '').toLowerCase();
               const isHighLevel = ['team leader', 'manager', 'social media manager', 'head'].some(r => rStr.includes(r) || dStr.includes(r));
               return isHighLevel || e.department?.toLowerCase() === 'hr';
            });
          } else {
            filteredEmployees = filteredEmployees.filter((e: any) => {
               const rStr = (e.role || '').toLowerCase();
               const dStr = (e.designation || '').toLowerCase();
               const isHighLevel = ['team leader', 'manager', 'social media manager', 'head'].some(r => rStr.includes(r) || dStr.includes(r));
               return !isHighLevel && e.department?.toLowerCase() !== 'hr' && rStr !== 'admin';
            });
          }
        } else if (isTeamLeader) {
         filteredEmployees = filteredEmployees.filter((e: any) => {
           const rStr = (e.role || '').toLowerCase();
           const dStr = (e.designation || '').toLowerCase();
           const isHighLevel = ['team leader', 'manager', 'social media manager', 'head'].some(r => rStr.includes(r) || dStr.includes(r));
           return e.id === user?.id || (!isHighLevel && rStr !== 'admin');
         });
       }
    }

    let datesToMap: Date[] = []
    if (dateRange?.from) {
      if (dateRange.to) {
        datesToMap = eachDayOfInterval({ start: dateRange.from, end: dateRange.to })
      } else {
        datesToMap = [dateRange.from]
      }
    }

    const mapped = datesToMap.flatMap(dateObj => {
      const dateStr = format(dateObj, "yyyy-MM-dd")
      return filteredEmployees.map((emp: any) => {
        const report = allReports.find((r: any) => r.employeeId === emp.id && r.date === dateStr)
        
        let isFullDayLeave = false
        if (leaveRequests?.length > 0) {
          const leaves = leaveRequests.filter((l: any) => l.employee_id === emp.id && l.status === 'Approved')
          isFullDayLeave = leaves.some((l: any) => {
            if (!l.start_date || !l.end_date) return false
            const [lsD, lsM, lsY] = l.start_date.split('-')
            const [leD, leM, leY] = l.end_date.split('-')
            const start = new Date(Number(lsY), Number(lsM)-1, Number(lsD))
            const end = new Date(Number(leY), Number(leM)-1, Number(leD))
            start.setHours(0,0,0,0)
            end.setHours(0,0,0,0)
            const current = new Date(dateObj)
            current.setHours(0,0,0,0)
            return current >= start && current <= end && l.day_type === 'Full Day'
          })
        }

        let avgRatingStr = ''
        if (isAdmin) {
           const empReports = allReports.filter((r: any) => r.employeeId === emp.id && r.rating)
           if (empReports.length > 0) {
              const total = empReports.reduce((sum: number, r: any) => sum + Number(r.rating), 0)
              avgRatingStr = (total / empReports.length).toFixed(1)
           }
        }

        let responsiblePerson = ''
        const eRoleStr = (emp.role || '').toLowerCase();
        const eDesigStr = (emp.designation || '').toLowerCase();
        const isHighLevel = ['team leader', 'manager', 'social media manager', 'head'].some(r => eRoleStr.includes(r) || eDesigStr.includes(r));
        if (isHighLevel || eRoleStr === 'admin') {
           responsiblePerson = 'HR / Admin'
        } else {
           const tls = employees.filter((e: any) => e.department?.toLowerCase() === emp.department?.toLowerCase() && e.role === 'Team Leader')
           if (tls.length > 0) {
             responsiblePerson = tls.map((t: any) => t.name || `${t.firstName} ${t.lastName}`).join(', ')
           } else {
             responsiblePerson = 'Admin'
           }
        }

        return {
          id: `${emp.id}-${dateStr}`,
          employeeId: emp.id,
          employeeName: emp.name || `${emp.firstName} ${emp.lastName}`,
          department: emp.department,
          role: emp.role,
          designation: emp.designation,
          date: dateStr,
          status: isFullDayLeave ? 'On Leave' : (report?.status || 'Pending Verification'),
          reportId: report?.id,
          rating: report?.rating ?? null,
          note: report?.note || '',
          verifiedBy: report?.userName || '',
          responsiblePerson
        }
      })
    })

    let resultList = mapped
    if (selectedStatusFilter !== 'all') {
      resultList = mapped.filter(item => item.status?.toLowerCase() === selectedStatusFilter.toLowerCase())
    }

    // Sort so that the highest rating is on top
    resultList.sort((a, b) => {
      const rA = Number(a.rating) || 0;
      const rB = Number(b.rating) || 0;
      return rB - rA;
    })

    return resultList
  }, [employees, allReports, leaveRequests, dateRange, user, isAdmin, isTeamLeader, activeDeptTab, activeRoleTab, selectedStatusFilter])

  const handleStatusUpdate = async (emp: any, newStatus: string) => {
    setIsSubmitting(true)
    try {
      const method = emp.reportId ? 'PUT' : 'POST'
      const url = emp.reportId 
        ? `${API_URL}/employee-daily-reports/${emp.reportId}` 
        : `${API_URL}/employee-daily-reports`

      const payload = emp.reportId 
        ? { 
            status: newStatus,
            performedBy: user?.id,
            userName: user?.name || `${user?.firstName} ${user?.lastName}`
          }
        : {
            employeeId: emp.employeeId,
            employeeName: emp.employeeName,
            department: emp.department,
            date: emp.date,
            status: newStatus,
            tasksCompleted: ["Work verified by TL"],
            tasksInProgress: [],
            hoursWorked: 8.0,
            performedBy: user?.id,
            userName: user?.name || `${user?.firstName} ${user?.lastName}`
          }

      const response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      if (response.ok) {
        toast.success(`Work ${newStatus.toLowerCase()} successfully`)
        refreshItem('employeeDailyReports')
      }
    } catch (error) {
      console.error('Error updating status:', error)
      toast.error('Failed to update status')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleVerify = async (status: string) => {
    if (!verifyRecord) return

    const numRating = Number(verifyRating)
    if (!verifyRating || isNaN(numRating) || numRating < 1 || numRating > 10) {
      toast.error("Please enter a compulsory rating between 1 and 10")
      return
    }

    if (!verifyNote || !verifyNote.trim()) {
      toast.error("Please enter a compulsory verification note")
      return
    }

    setIsSubmitting(true)
    try {
      const method = verifyRecord.reportId ? 'PUT' : 'POST'
      const url = verifyRecord.reportId 
        ? `${API_URL}/employee-daily-reports/${verifyRecord.reportId}` 
        : `${API_URL}/employee-daily-reports`

      const payload = verifyRecord.reportId 
        ? { 
            status: status,
            note: verifyNote,
            rating: Number(verifyRating),
            performedBy: user?.id,
            userName: user?.name || `${user?.firstName} ${user?.lastName}`
          }
        : {
            employeeId: verifyRecord.employeeId,
            employeeName: verifyRecord.employeeName,
            department: verifyRecord.department,
            date: verifyRecord.date,
            status: status,
            tasksCompleted: ["Work verified by TL"],
            tasksInProgress: [],
            hoursWorked: 8.0,
            note: verifyNote,
            rating: Number(verifyRating),
            performedBy: user?.id,
            userName: user?.name || `${user?.firstName} ${user?.lastName}`
          }

      const response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      if (response.ok) {
        toast.success(`Work ${status.toLowerCase()} successfully`)
        setVerifyRecord(null)
        setVerifyNote('')
        setVerifyRating('')
        refreshItem('employeeDailyReports')
      } else {
        toast.error('Failed to verify work')
      }
    } catch (error) {
      console.error('Error verifying work:', error)
      toast.error('Failed to verify work')
    } finally {
      setIsSubmitting(false)
    }
  }

  const fetchLogs = async (reportId?: string, employeeName?: string) => {
    setLogsOpen(true)
    setLogFilter({ reportId, employeeName })
    if (!reportId) {
      setReportLogs([])
      return
    }
    setIsLoadingLogs(true)
    try {
      const url = `${API_URL}/task-logs?dailyReportId=${reportId}`
      const res = await fetch(url)
      if (res.ok) {
        setReportLogs(await res.json())
      }
    } catch (err) {
      console.error("Error fetching logs:", err)
    } finally {
      setIsLoadingLogs(false)
    }
  }

  const columns = [
    { key: 'employeeName' as const, header: 'Employee', render: (record: any) => (
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center text-slate-500 font-bold text-xs">
          {record.employeeName?.charAt(0)}
        </div>
        <span className="font-medium text-slate-700">{record.employeeName}</span>
      </div>
    )},
    { key: 'department' as const, header: 'Department', render: (record: any) => (
      <span className="px-2 py-0.5 bg-slate-50 text-slate-500 border border-slate-100 rounded text-[10px] font-bold uppercase tracking-wider">
        {record.department}
      </span>
    )},
    { key: 'date' as const, header: 'Date', render: (record: any) => (
      <div className="text-slate-500 text-[11px] font-medium flex items-center gap-1">
        <Clock className="w-3 h-3" /> {record.date}
      </div>
    )},
    { key: 'status' as const, header: 'Work Status', render: (record: any) => {
      const s = (record.status || '').toLowerCase();
      return (
        <span className={`px-2 py-1 rounded text-[10px] font-black uppercase tracking-tight ${
          s === 'approved' ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' : 
          s === 'rejected' ? 'bg-rose-50 text-rose-600 border border-rose-100' : 
          'bg-amber-50 text-amber-600 border border-amber-100'
        }`}>
          {record.status}
        </span>
      );
    }},
    { key: 'note' as const, header: 'Verification Note', render: (record: any) => (
      <div className="flex flex-col gap-1">
        {record.note ? (
          <span 
            className="text-[11px] text-slate-600 font-medium block" 
            style={{ wordBreak: 'normal', overflowWrap: 'break-word', whiteSpace: 'normal', maxWidth: '200px' }} 
            title={record.note}
          >
            {record.note}
            {record.rating && <span className="block text-brand-teal font-bold mt-0.5">Rating: {record.rating}/10</span>}
          </span>
        ) : (
          <span className="text-[10px] text-slate-400 italic">No note added</span>
        )}
      </div>
    )},
    { key: 'rating' as const, header: 'Rating', render: (record: any) => {
      const hasRating = record.rating != null && record.rating !== '' && !isNaN(Number(record.rating)) && Number(record.rating) > 0;
      return hasRating ? (
        <span className="text-[10px] font-bold text-amber-500 bg-amber-50 px-2 py-1 rounded border border-amber-100 inline-flex items-center gap-1 w-max shadow-sm">
          ⭐ {record.rating}/10
        </span>
      ) : (
        <span className="text-[10px] text-slate-400 italic">-</span>
      );
    }},
    { key: 'verifiedBy' as const, header: 'Verified By / Responsible', render: (record: any) => (
      record.status === 'Pending Verification' ? (
        <span className="text-[11px] text-slate-600 font-semibold">{record.responsiblePerson}</span>
      ) : record.verifiedBy ? (
        <span className="text-[11px] font-bold text-brand-teal">
          {record.verifiedBy}
        </span>
      ) : (
        <span className="text-[11px] text-slate-600 font-semibold">{record.responsiblePerson}</span>
      )
    )},
  ]

  const actions = (record: any) => {
    const isSelf = user?.id === record.employeeId;
    const recordRole = record.role?.toLowerCase() || '';
    const recordDesig = record.designation?.toLowerCase() || '';
    const isHighLevelRole = ['team leader', 'manager', 'social media manager', 'head'].some(r => recordRole.includes(r) || recordDesig.includes(r));
    
    let hasAccess = false;
    if (isAdmin || isHRUser) {
      hasAccess = true;
    } else if (isTeamLeader) {
      hasAccess = user?.department === record.department && !isHighLevelRole;
    } else if (checkPermission('daily-progress', 'canEdit')) {
      hasAccess = true;
    }
    
    const canManage = hasAccess && !isSelf;
    
    if (record.status === 'On Leave') {
        return <span className="text-[10px] text-slate-400 italic font-medium tracking-tighter">On Leave</span>
    }
    if (!canManage) {
        return <span className="text-[10px] text-slate-400 italic font-medium tracking-tighter">
          View Only
        </span>
    }

    return (
      <div className="flex items-center gap-2">
        <Button 
          size="sm" 
          variant="outline"
          className="h-7 px-3 text-[10px] font-bold border-slate-200 text-slate-600 hover:bg-slate-50"
          onClick={() => {
            setVerifyRecord(record)
            setVerifyNote(record.note || '')
            setVerifyRating(record.rating ? String(record.rating) : '')
          }}
        >
          <CheckCircle2 className="w-3 h-3 mr-1" /> Verify
        </Button>
        <Button 
          size="sm" 
          variant="outline"
          className="h-7 w-7 p-0 text-slate-500 hover:bg-slate-50 border-slate-200 flex items-center justify-center"
          onClick={(e) => {
            e.stopPropagation()
            fetchLogs(record.reportId, record.employeeName)
          }}
          title="View History"
        >
          <History className="w-3.5 h-3.5" />
        </Button>
      </div>
    )
  }

  if (permissionsLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="w-8 h-8 animate-spin text-brand-teal" />
      </div>
    );
  }

  if (!canViewDailyProgress) {
    return (
      <div className="flex items-center justify-center min-h-[400px] text-slate-500">
        You do not have permission to view daily progress.
      </div>
    );
  }

  return (
    <div className="space-y-6 mt-4">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-white p-4 rounded-2xl shadow-sm border border-slate-200">
        <div className="flex items-center gap-3 bg-slate-50 p-1.5 px-3 rounded-xl border border-slate-200">
          <Label className="text-[10px] font-black uppercase text-slate-400 tracking-widest whitespace-nowrap">Target Date(s)</Label>
          <div className="h-4 w-px bg-slate-200 mx-1" />
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="ghost" className="h-8 px-2 text-xs font-bold text-brand-teal hover:bg-slate-100 p-0 shadow-none border-none">
                <Calendar className="w-3.5 h-3.5 mr-2" />
                {dateRange?.from ? (
                  dateRange.to ? (
                    `${format(dateRange.from, "dd/MM/yyyy")} - ${format(dateRange.to, "dd/MM/yyyy")}`
                  ) : (
                    format(dateRange.from, "dd/MM/yyyy")
                  )
                ) : (
                  "Select Date Range"
                )}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start">
              <CalendarComponent
                mode="range"
                selected={dateRange}
                onSelect={setDateRange}
                initialFocus
              />
            </PopoverContent>
          </Popover>
        </div>

        {/* Department and Role Tabs */}
        {(isAdmin || isTeamLeader || isHRUser) && (
          <div className="flex items-center gap-4">
            {isAdmin && (
              <Button
                onClick={() => router.push('/ratings')}
                className="bg-amber-50 text-amber-600 hover:bg-amber-100 border border-amber-200 shadow-sm h-9 px-4 text-xs font-bold whitespace-nowrap"
              >
                <Star className="w-3.5 h-3.5 mr-2" />
                View Ratings
              </Button>
            )}
            {isTeamLeader && !isAdmin && (
              <Button 
                variant="outline" 
                className="h-9 px-4 text-xs font-bold border-brand-teal text-brand-teal hover:bg-brand-teal/10 shadow-sm"
                onClick={() => router.push('/ratings')}
              >
                All Ratings
              </Button>
            )}
            {isTeamLeader && !isAdmin && (
              <div className="flex items-center gap-1 bg-slate-100/70 p-1 rounded-xl shadow-inner border border-slate-200/60">
                <button
                  onClick={() => setTlViewMode('my')}
                  className={`px-4 py-2 rounded-lg text-[13px] font-bold transition-all flex items-center gap-2 ${
                    tlViewMode === 'my' 
                      ? 'bg-white text-brand-teal shadow-sm border border-slate-200/50' 
                      : 'text-slate-500 hover:text-slate-800 hover:bg-slate-200/50'
                  }`}
                >
                  <User className="w-4 h-4" /> My Progress
                </button>
                <button
                  onClick={() => setTlViewMode('team')}
                  className={`px-4 py-2 rounded-lg text-[13px] font-bold transition-all flex items-center gap-2 ${
                    tlViewMode === 'team' 
                      ? 'bg-white text-brand-teal shadow-sm border border-slate-200/50' 
                      : 'text-slate-500 hover:text-slate-800 hover:bg-slate-200/50'
                  }`}
                >
                  <Users className="w-4 h-4" /> Team Progress
                </button>
              </div>
            )}
            {!defaultDepartment && !isAdmin && !isHRUser && (
              <div className="flex items-center gap-2 overflow-x-auto pb-1 sm:pb-0 hide-scrollbar">
                <button className="px-4 py-2 rounded-lg text-[13px] font-bold flex items-center transition-all whitespace-nowrap bg-brand-teal text-white shadow-sm cursor-default">
                  {user?.department}
                </button>
              </div>
            )}

            {(isAdmin || isHRUser) && (
              <div className="flex items-center gap-2 overflow-x-auto pb-1 sm:pb-0 hide-scrollbar">
                <button
                  onClick={() => setActiveRoleTab('Team Leaders')}
                  className={`px-4 py-2 rounded-lg text-sm font-bold flex items-center transition-all whitespace-nowrap ${
                    activeRoleTab === 'Team Leaders'
                      ? "bg-brand-teal text-white shadow-sm"
                      : "text-slate-500 hover:text-slate-800 bg-slate-50 border border-slate-200 hover:border-slate-300"
                  }`}
                >
                  Team Leaders & HR
                </button>
                <button
                  onClick={() => setActiveRoleTab('Employees')}
                  className={`px-4 py-2 rounded-lg text-sm font-bold flex items-center transition-all whitespace-nowrap ${
                    activeRoleTab === 'Employees'
                      ? "bg-brand-teal text-white shadow-sm"
                      : "text-slate-500 hover:text-slate-800 bg-slate-50 border border-slate-200 hover:border-slate-300"
                  }`}
                >
                  Employees
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
        <DataTable
          data={isTeamLeader && !isAdmin && !isHRUser ? displayData.filter(d => tlViewMode === 'my' ? String(d.employeeId) === String(user?.id) : String(d.employeeId) !== String(user?.id)) : displayData}
          columns={columns}
          actions={actions}
          searchKey="employeeName"
          searchPlaceholder={tlViewMode === 'my' ? "Filter dates..." : "Filter employees..."}
          extraFilters={
            <div className="flex items-center gap-2">
              <Select value={selectedStatusFilter} onValueChange={setSelectedStatusFilter}>
                <SelectTrigger className="h-9 w-[160px] text-xs font-semibold bg-white border border-slate-200">
                  <SelectValue placeholder="All Statuses" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Statuses</SelectItem>
                  <SelectItem value="Approved">Approved</SelectItem>
                  <SelectItem value="Rejected">Rejected</SelectItem>
                  <SelectItem value="Pending Verification">Pending Verification</SelectItem>
                </SelectContent>
              </Select>
            </div>
          }
        />
      </div>

      <Dialog open={!!verifyRecord} onOpenChange={(open) => !open && setVerifyRecord(null)}>
        <DialogContent className="sm:max-w-[1000px] bg-white rounded-2xl shadow-xl border border-slate-100 p-6 max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="text-base font-bold text-slate-800 flex items-center gap-2">
              <CheckCircle2 className="w-5 h-5 text-brand-teal" />
              <span>Verify Daily Progress</span>
            </DialogTitle>
            <div className="text-[11px] text-slate-400 font-medium tracking-tight mt-1">
              Verify work for <span className="font-bold text-slate-600">{verifyRecord?.employeeName}</span> on <span className="font-bold text-slate-600">{verifyRecord?.date}</span>.
            </div>
          </DialogHeader>

          <div className="py-4 space-y-5">
            {(() => {
              const selectedAttendance = verifyRecord ? attendanceRecords.find((a: any) => a.employeeId === verifyRecord.employeeId && a.date === verifyRecord.date) : null;
              const workLogs = selectedAttendance?.punches || [];

              const employeeDept = verifyRecord?.department?.toLowerCase() || '';
              const isSmm = (dept: string) => ['smm', 'creative'].includes(dept);
              const isDm = (dept: string) => dept === 'digital marketing' || dept === 'dm';
              
              const filteredPending = pendingTasks.filter(t => {
                const taskDept = t.department?.toLowerCase() || '';
                if (!employeeDept) return true;
                if (isSmm(employeeDept) && isSmm(taskDept)) return true;
                if (isDm(employeeDept) && isDm(taskDept)) return true;
                return taskDept === employeeDept;
              });

              return (
                <>
                  <div className="border border-slate-200 rounded-xl overflow-hidden bg-slate-50 max-h-[500px] overflow-y-auto">
                    {verifyRecord && <MyTasksView targetUserId={verifyRecord?.employeeId} isEmbedded={true} targetDate={verifyRecord?.date} />}
                  </div>
                </>
              );
            })()}

            <div>
              <Label className="text-xs font-bold text-slate-700 mb-2 block">Rating (1 to 10) <span className="text-rose-500">*</span></Label>
              <Input
                type="number"
                min="1"
                max="10"
                step="any"
                value={verifyRating}
                onChange={(e) => {
                  const val = e.target.value;
                  if (val === '') {
                    setVerifyRating('');
                    return;
                  }
                  const num = Number(val);
                  if (num > 10) {
                    setVerifyRating('10');
                  } else if (num < 0) {
                    setVerifyRating('1');
                  } else {
                    setVerifyRating(val);
                  }
                }}
                placeholder="Enter rating from 1 to 10"
                className="text-xs border-slate-200 focus:border-brand-teal focus:ring-brand-teal rounded-xl h-10"
              />
            </div>

            <div>
              <Label className="text-xs font-bold text-slate-700 mb-2 block">Verification Note <span className="text-rose-500">*</span></Label>
              <Textarea
                value={verifyNote}
                onChange={(e) => setVerifyNote(e.target.value)}
                placeholder="Enter verification note (compulsory)..."
                className="min-h-[100px] text-xs resize-none border-slate-200 focus:border-brand-teal focus:ring-brand-teal rounded-xl p-3"
              />
            </div>
          </div>

          <DialogFooter className="flex items-center justify-end gap-2 border-t border-slate-50 pt-4 mt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setVerifyRecord(null)}
              className="h-9 text-xs font-bold border-slate-200 text-slate-500 rounded-lg px-4"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => handleVerify('Rejected')}
              disabled={isSubmitting}
              className="h-9 text-xs font-bold bg-rose-50 hover:bg-rose-100 text-rose-600 rounded-lg shadow-sm border border-rose-200 px-4"
            >
              {isSubmitting ? 'Processing...' : 'Reject'}
            </Button>
            <Button
              size="sm"
              onClick={() => handleVerify('Approved')}
              disabled={isSubmitting}
              className="h-9 text-xs font-bold bg-brand-teal hover:bg-brand-teal-light text-white rounded-lg shadow-sm px-4"
            >
              {isSubmitting ? 'Processing...' : 'Approve'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={ratingsModalOpen} onOpenChange={setRatingsModalOpen}>
        <DialogContent className="sm:max-w-[700px] bg-white rounded-2xl shadow-xl border border-slate-100 p-6 max-h-[85vh] flex flex-col">
          <DialogHeader className="flex flex-row justify-between items-center mb-2 flex-shrink-0">
            <DialogTitle className="text-base font-bold text-slate-800">
              Employee Ratings Overview
            </DialogTitle>
            <Select value={ratingDateFilter} onValueChange={(val: any) => setRatingDateFilter(val)}>
              <SelectTrigger className="w-[160px] h-9 text-xs font-semibold bg-white border border-slate-200">
                <SelectValue placeholder="Filter Date" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Time</SelectItem>
                <SelectItem value="this_week">This Week</SelectItem>
                <SelectItem value="yesterday">Yesterday</SelectItem>
              </SelectContent>
            </Select>
          </DialogHeader>

          <div className="overflow-y-auto border border-slate-200 rounded-xl">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 sticky top-0 shadow-sm">
                <tr>
                  <th className="px-4 py-3 font-semibold text-slate-500 text-[11px] uppercase tracking-wider">Employee</th>
                  <th className="px-4 py-3 font-semibold text-slate-500 text-[11px] uppercase tracking-wider">Department</th>
                  <th className="px-4 py-3 font-semibold text-slate-500 text-[11px] uppercase tracking-wider text-center">Days Verified</th>
                  <th className="px-4 py-3 font-semibold text-slate-500 text-[11px] uppercase tracking-wider text-right">Avg Rating</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {ratingData.length > 0 ? (
                  ratingData.map((emp: any, i: number) => (
                    <tr key={i} className="hover:bg-slate-50/50 transition-colors">
                      <td className="px-4 py-3 font-bold text-slate-700 text-xs">{emp.name}</td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 bg-slate-100 text-slate-500 border border-slate-200 rounded text-[10px] font-bold uppercase tracking-wider">
                          {emp.department}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-center text-slate-600 font-semibold text-xs">{emp.count}</td>
                      <td className="px-4 py-3 text-right">
                        <span className="px-2.5 py-1 bg-amber-50 text-amber-600 border border-amber-200 rounded-lg font-black text-xs shadow-sm min-w-[50px] inline-block text-center">
                          {emp.avgRating}
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-slate-400 italic text-sm">
                      No ratings found for the selected period.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </DialogContent>
      </Dialog>

      <ActivityLogDialog 
        open={logsOpen}
        onOpenChange={setLogsOpen}
        title="Verification History"
        subtitle={logFilter.employeeName ? `Activity logs for ${logFilter.employeeName}` : undefined}
        logs={reportLogs}
        isLoading={isLoadingLogs}
      />
    </div>
  )
}
