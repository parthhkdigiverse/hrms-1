"use client";

import React, { useState, useEffect } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, SelectGroup, SelectLabel, SelectSeparator } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Loader2 } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { API_URL } from "@/lib/config";
import dayjs from "dayjs";

interface PunchInModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (data: { type: string; subtype?: string; value?: string; taskId?: string }) => void;
  userId: string;
  initialActivityType?: string;
  initialActivitySubtype?: string;
  initialActivityValue?: string;
  initialTaskId?: string;
  isUpdateMode?: boolean;
}

export function PunchInModal({ open, onOpenChange, onConfirm, userId, initialActivityType, initialActivitySubtype, initialActivityValue, initialTaskId, isUpdateMode }: PunchInModalProps) {
  const [selectedTab, setSelectedTab] = useState<string>("today_work");
  
  const [activityValue, setActivityValue] = useState<string>("");
  const [taskId, setTaskId] = useState<string>("");
  const [customTaskName, setCustomTaskName] = useState<string>("");
  
  let userDept = "";
  let userRole = "";
  if (typeof window !== "undefined") {
    const userStr = localStorage.getItem("user");
    if (userStr) {
      try {
        const parsed = JSON.parse(userStr);
        userDept = (parsed.department || "").toLowerCase().trim();
        userRole = parsed.role || "";
      } catch (e) {}
    }
  }
  const isHR = userDept === 'hr' || userDept.includes('hr') || userDept.includes('human resources') || userDept.includes('human-resources');
  
  const [tasks, setTasks] = useState<any[]>([]);
  const [settings, setSettings] = useState<any>(null);
  const [pastResearch, setPastResearch] = useState<string[]>([]);
  const [pastWorkTasks, setPastWorkTasks] = useState<string[]>([]);
  const [isNewResearch, setIsNewResearch] = useState(false);
  const [isNewWorkTask, setIsNewWorkTask] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (open && userId) {
      fetchData();
      
      let initialTab = "today_work";
      const isDigitalMarketing = ['digital marketing', 'dm'].includes(userDept);
      
      if (isUpdateMode && initialActivityType) {
        if (initialActivityType === "Work") {
          if (userDept.includes('sales')) {
            initialTab = "hr_sales_work";
          } else {
            initialTab = isDigitalMarketing ? "assigned_brands" : "today_work";
          }
        } else if (initialActivityType === "Research") {
          initialTab = "research";
        } else if (initialActivityType === "Other" && initialActivitySubtype) {
          initialTab = `other_${initialActivitySubtype}`;
        }
      } else {
        if (userDept.includes('sales')) initialTab = "hr_sales_work";
        else if (isDigitalMarketing) initialTab = "assigned_brands";
      }
      
      setSelectedTab(initialTab);
      
      setActivityValue(initialActivityValue || "");
      setTaskId(initialTaskId || "");
      if (initialActivityType === "Research" && initialActivityValue) {
        setIsNewResearch(true);
      }
    }
  }, [open, userId, initialActivityType, initialActivitySubtype, initialActivityValue, initialTaskId, isUpdateMode, userDept]);

  const fetchData = async () => {
    setIsLoading(true);
    try {
      const [tasksRes, settingsRes, attRes, globalProjRes] = await Promise.all([
        fetch(`${API_URL}/wm-tasks`),
        fetch(`${API_URL}/system-settings`),
        fetch(`${API_URL}/attendance?userId=${userId}`),
        fetch(`${API_URL}/projects`)
      ]);

      let allProjects: any[] = [];
      if (globalProjRes.ok) {
        allProjects = await globalProjRes.json();
      }

      if (settingsRes.ok) {
        setSettings(await settingsRes.json());
      }
      if (attRes.ok) {
        const allAtt = await attRes.json();
        const uniqueTitles = new Set<string>();
        const uniqueWorkTitles = new Set<string>();
        allAtt.forEach((a: any) => {
          if (a.employeeId === userId && Array.isArray(a.punches)) {
            a.punches.forEach((punch: any) => {
              if (punch.activityType === "Research" && punch.activityValue) {
                uniqueTitles.add(punch.activityValue);
              } else if (punch.activityType === "Work" && punch.activityValue && !punch.taskId) {
                uniqueWorkTitles.add(punch.activityValue);
              }
            });
          }
        });
        setPastResearch(Array.from(uniqueTitles));
        setPastWorkTasks(Array.from(uniqueWorkTitles));
      }
      if (tasksRes.ok) {
        let allTasks = [];
        const roleParam = userRole ? `&role=${encodeURIComponent(userRole)}` : '';
        if (isHR) {
          const genTasksRes = await fetch(`${API_URL}/tasks?userId=${userId}${roleParam}&limit=10000`);
          if (genTasksRes.ok) {
            allTasks = await genTasksRes.json();
          }
          // Also include wm-tasks for HR users (they may have dev tasks assigned)
          try {
            const wmData = await tasksRes.json();
            allTasks = [...allTasks, ...wmData];
          } catch (e) {
            // tasksRes body may already be consumed if it failed, ignore
          }
        } else {
          allTasks = await tasksRes.json();
          try {
            const genTasksRes = await fetch(`${API_URL}/tasks?userId=${userId}${roleParam}&limit=10000`);
            if (genTasksRes.ok) {
              const genTasks = await genTasksRes.json();
              allTasks = [...allTasks, ...genTasks];
            }
          } catch (e) {
            console.error("Error fetching general tasks:", e);
          }
        }
        let myTasks = allTasks.filter((t: any) => {
          const isAssigned = String(t.assignedToId) === String(userId) || 
                             (t.assignedToIds && t.assignedToIds.map(String).includes(String(userId)));
          const isHRTask = t.department === 'HR' || t.department?.toUpperCase() === 'HR';
          const canShow = isAssigned || (isHR && isHRTask);
          if (!canShow) return false;
          const statusLower = (t.status || "").toLowerCase().trim();
          if (statusLower === "completed" || statusLower === "onhold" || statusLower === "on hold" || statusLower === "approved") return false;
          
          if (t.projectId) {
            const project = allProjects.find((p: any) => String(p.id || p._id) === String(t.projectId));
            if (project) {
              const pStatus = (project.status || "").toLowerCase().trim();
              if (pStatus === "onhold" || pStatus === "on-hold" || pStatus === "on hold") return false;
            }
          }
          return true;
        });

        // Fetch SMM tasks if applicable
        try {
          const userStr = localStorage.getItem("user");
          const userObj = userStr ? JSON.parse(userStr) : null;
          const userDept = (userObj?.department || "").toLowerCase().trim();
          const isCreativeUser = ['creative', 'smm', 'social media marketing', 'graphics'].includes(userDept);
          const isDigitalMarketingUser = ['digital marketing', 'dm'].includes(userDept);

          if (isCreativeUser || isDigitalMarketingUser) {
            const [ccRes, owRes, clientRes, transferRes] = await Promise.all([
              fetch(`${API_URL}/content-calendar/all`),
              fetch(`${API_URL}/other-work/all`),
              fetch(`${API_URL}/clients`),
              fetch(`${API_URL}/work-transfer-requests`)
            ]);
            
            if (ccRes.ok && owRes.ok && clientRes.ok) {
              const [ccList, owList, clientList, transferListRaw] = await Promise.all([ccRes.json(), owRes.json(), clientRes.json(), transferRes.ok ? transferRes.json() : []]);
              const projList = allProjects;
              const acceptedTransfers = (Array.isArray(transferListRaw) ? transferListRaw : []).filter((r: any) => r.status === 'Accepted');
              const smmTasks: any[] = [];
              if (isCreativeUser) {
                const myOw = owList.filter((o: any) => {
                  const transfer = acceptedTransfers.find((t: any) => String(t.taskId) === String(o.id || o._id) && (t.taskType === 'other-work' || t.taskType === 'creative'));
                  const currentAssigneeId = transfer ? transfer.receiverId : o.assigneeId;
                  return String(currentAssigneeId).trim() === String(userId).trim() && o.status !== 'Approved';
                });
                myOw.forEach((o: any) => {
                  const project = projList.find((p: any) => String(p.id || p._id).trim() === String(o.projectId).trim());
                  if (project) {
                    const pStatus = (project.status || "").toLowerCase().trim();
                    if (pStatus === "onhold" || pStatus === "on-hold" || pStatus === "on hold") return;
                  }
                  const client = clientList.find((c: any) => String(c.id || c._id).trim() === String(o.clientId).trim());
                  let displayName = "Other Work";
                  if (o.taskType === 'digital-marketing') displayName = 'Digital Marketing';
                  else if (client) displayName = project ? `${client.companyName || client.clientName} (${project.projectName})` : (client.companyName || client.clientName);

                  smmTasks.push({
                    id: o.id || o._id,
                    title: o.title || o.taskName || 'Other Work Task',
                    projectName: displayName,
                    dueDate: o.deadline || "",
                    status: o.status
                  });
                });
              }
              
              if (isDigitalMarketingUser) {
                const dmProjects = projList.filter((p: any) => {
                  if (p.department && p.department.trim().toLowerCase() === 'digital marketing') {
                    const pStatus = (p.status || "").toLowerCase().trim();
                    if (pStatus !== "active") return false;
                    return true;
                  }
                  return false;
                });
                const myProjects = dmProjects.filter((p: any) => {
                  const isOriginalAssignee = String(p.assignedEmployeeId).trim() === String(userId).trim();
                  const isTransferredToMe = acceptedTransfers.some((t: any) => String(t.taskId) === String(p.id || p._id) && String(t.receiverId) === String(userId));
                  return isOriginalAssignee || isTransferredToMe;
                });
                
                myProjects.forEach((p: any) => {
                  const client = clientList.find((c: any) => String(c.id || c._id) === String(p.clientId));
                  const cName = client?.companyName || client?.clientName || p.clientName || "Unknown Client";
                  smmTasks.push({
                    id: p.id || p._id,
                    title: cName,
                    projectName: p.projectName || p.title || "",
                    dueDate: p.endDate || p.deadline || "",
                    status: "pending"
                  });
                });
                
                const myOw = owList.filter((o: any) => {
                  const transfer = acceptedTransfers.find((t: any) => String(t.taskId) === String(o.id || o._id) && t.taskType === 'dm-other-work');
                  const currentAssigneeId = transfer ? transfer.receiverId : o.assigneeId;
                  return String(currentAssigneeId).trim() === String(userId).trim() && o.status !== 'Approved' && o.taskType === 'dm-other-work';
                });
                myOw.forEach((o: any) => {
                  const project = projList.find((p: any) => String(p.id || p._id).trim() === String(o.projectId).trim());
                  if (project) {
                    const pStatus = (project.status || "").toLowerCase().trim();
                    if (pStatus === "onhold" || pStatus === "on-hold" || pStatus === "on hold") return;
                  }
                  smmTasks.push({
                    id: o.id || o._id,
                    title: o.title || o.taskName || 'Other Work Task',
                    projectName: 'Other Work',
                    dueDate: o.deadline || "",
                    status: o.status,
                    isDmOtherWork: true
                  });
                });
              }
              
              if (isCreativeUser) {
                ccList.forEach((entry: any) => {
                  const client = clientList.find((c: any) => String(c.id || c._id).trim() === String(entry.clientId).trim());
                  // In SMM, CC tasks use the client's Creative project
                  const project = projList.find((p: any) => String(p.clientId).trim() === String(entry.clientId).trim() && p.department?.toLowerCase().trim() === 'creative');
                  if (!project) return; // Only show if active creative project (matching SMM)
                  const pStatus = (project.status || "").toLowerCase().trim();
                  if (pStatus === "onhold" || pStatus === "on-hold" || pStatus === "on hold") return;
                  
                  const cName = client?.companyName || client?.clientName || "Unknown Client";
                  
                  const checkStage = (stageName: string, idField: string, dateField: string, linkField: string, linkCheck?: (e:any)=>boolean) => {
                    const originalAssigneeId = entry[idField] || project?.[idField] || client?.[idField];
                    const isDone = linkCheck ? linkCheck(entry) : (!!entry[linkField] && entry[linkField] !== '-');
                    
                    const hasApplicableRemark = entry.remark && entry.remark.trim() !== '' && (
                      !entry.remarkStage || 
                      (() => {
                        const stages = ['Script', 'Shoot', 'Caption', 'Thumbnail', 'Editing', 'Post/Graphics', 'Approval', 'Posting'];
                        const idx1 = stages.indexOf(stageName === 'Editing' && entry.postReel === 'Post' ? 'Post/Graphics' : stageName);
                        const idx2 = stages.indexOf(entry.remarkStage);
                        return idx1 >= idx2;
                      })()
                    );
                    const isClientIssue = hasApplicableRemark && entry.remark.startsWith('[CLIENT ISSUE] ');
                    if (isClientIssue) return; // SMM moves these to Pending Work

                    const transfer = acceptedTransfers.find((t: any) => String(t.taskId) === String(entry.id || entry._id) && t.stage === (stageName === 'Editing' && entry.postReel === 'Post' ? 'Post/Graphics' : stageName));
                    const currentAssigneeId = transfer ? transfer.receiverId : originalAssigneeId;

                    if (String(currentAssigneeId).trim() === String(userId).trim() && !isDone) {
                      let dateStr = entry[dateField];
                      if (!dateStr && (stageName === 'Caption' || stageName === 'Thumbnail')) {
                        dateStr = entry.editingStart;
                      }
                      
                      if (!dateStr || dateStr === '-') return; // SMM strictly requires a valid date for CC tasks

                      const taskName = entry.concept || entry.topic || (entry.postReel ? `${entry.postReel} Content` : `Task for ${entry.postingDate || entry.monthYear || 'Unknown Date'}`);
                      
                      smmTasks.push({
                        id: `${entry.id || entry._id}-${stageName}`,
                        title: taskName,
                        projectName: `${stageName} - ${cName}`,
                        dueDate: dateStr,
                        status: "pending"
                      });
                    }
                  };
                  
                  const isPost = entry.postReel === "Post";
                  const isStory = entry.postReel === "Story";
                  if (!isPost && !isStory) checkStage('Script', 'assignedScriptwriterId', 'scriptDate', 'scriptLink');
                  if (!isPost && !isStory) checkStage('Shoot', 'assignedShooterId', 'shootDate', 'shootLink');
                  if (!isStory) checkStage('Caption', 'assignedCaptionWriterId', 'captionDate', 'caption');
                  if (!isPost && !isStory) checkStage('Thumbnail', 'assignedThumbnailDesignerId', 'thumbnailDate', 'thumbnailLink');
                  
                  const editIdField = isPost ? 'assignedPostDesignerId' : 'assignedReelEditorId';
                  const editLinkField = isPost ? 'finalPostLink' : 'finalReelLink';
                  checkStage('Editing', editIdField, 'editingStart', editLinkField);
                  checkStage('Approval', 'assignedApproverId', 'approval', 'isApproved', (e) => e.isApproved === 'Yes');
                  if (!isStory) checkStage('Posting', 'assignedPosterId', 'postingDate', 'postingLinkOfIg');
                });
              }
              
              myTasks = [...myTasks, ...smmTasks];
            }
          }
        } catch (e) {
          console.error("Error fetching SMM tasks", e);
        }

        setTasks(myTasks);
      }
    } catch (err) {
      console.error("Error fetching data for punch in modal:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleConfirm = async () => {
    let type = "Work";
    let subtype = "";

    if (selectedTab === "today_work" || selectedTab === "upcoming_work" || selectedTab === "assigned_brands" || selectedTab === "hr_sales_work" || selectedTab === "dm_other_work" || selectedTab === "pending_task") {
      type = "Work";
    } else if (selectedTab === "research") {
      type = "Research";
    } else if (selectedTab.startsWith("other_")) {
      type = "Other";
      subtype = selectedTab.replace("other_", "");
    }

    const data: any = { type };
    if (type === "Work") {
      if (selectedTab === "hr_sales_work" && !isNewWorkTask) {
        if (taskId) {
          // User selected an actual assigned task
          data.taskId = taskId;
          const selectedTask = tasks.find(t => t.id === taskId);
          data.value = selectedTask ? selectedTask.title : activityValue;
        } else {
          // User selected a past work task (free-text)
          data.taskId = undefined;
          data.value = activityValue;
        }
      } else if (selectedTab === "dm_other_work" && !isNewWorkTask) {
        data.taskId = taskId;
        const selectedTask = tasks.find(t => t.id === taskId);
        if (selectedTask) {
          data.value = selectedTask.title;
        } else {
          data.value = activityValue || "Other Work";
        }
      } else if (taskId === "custom" || (selectedTab === "dm_other_work" && isNewWorkTask) || (selectedTab === "hr_sales_work" && isNewWorkTask)) {
        setIsLoading(true);
        try {
          const userStr = localStorage.getItem("user");
          const userObj = userStr ? JSON.parse(userStr) : {};
          const userName = userObj.name || (userObj.firstName ? `${userObj.firstName} ${userObj.lastName || ''}`.trim() : "Unknown User");
          const deptStr = (userObj.department || "").toLowerCase();
          const desigStr = (userObj.designation || "").toLowerCase();
          const isDM = deptStr.includes('marketing') || deptStr.includes('dm') || desigStr.includes('marketing');
          
          const titleToUse = selectedTab === "dm_other_work" ? activityValue : (selectedTab === "hr_sales_work" ? activityValue : (customTaskName || activityValue));
          
          const payload = {
            title: titleToUse,
            description: "Custom task created from Punch-In",
            assigneeId: String(userId),
            assigneeName: userName,
            assignerId: String(userId),
            assignerName: userName,
            deadline: new Date().toISOString().split('T')[0],
            status: "In Progress",
            taskType: (isDM || selectedTab === "dm_other_work") ? "dm-other-work" : "other-work"
          };
          
          const isDev = userDept.includes('development');
          const isSales = userDept.includes('sales');
          let url = isDev && selectedTab !== "dm_other_work" ? `${API_URL}/wm-tasks` : `${API_URL}/other-work`;
          if (isSales && selectedTab === "hr_sales_work") {
            url = `${API_URL}/tasks`;
          }

          let bodyPayload;
          if (isDev && selectedTab !== "dm_other_work") {
            bodyPayload = {
              title: titleToUse,
              description: "Custom task created from Punch-In",
              projectId: "custom",
              projectName: "Custom Task",
              assignedToId: userId,
              assignedToName: userName,
              department: "Development",
              dueDate: new Date().toISOString().split('T')[0],
              status: "in-progress",
              priority: "medium",
              performedBy: userId,
              userName: userName
            };
          } else if (isSales && selectedTab === "hr_sales_work") {
            bodyPayload = {
              title: titleToUse,
              description: "Custom task created from Punch-In",
              dueDate: new Date().toISOString().split('T')[0],
              status: "in-progress",
              priority: "medium",
              assignedToIds: [userId],
              performedBy: userId,
              userName: userName,
              department: "Sales"
            };
          } else {
            bodyPayload = payload;
          }
          
          const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(bodyPayload)
          });
          
          if (res.ok) {
            const newWork = await res.json();
            data.taskId = newWork.id || newWork._id;
            data.value = titleToUse;
          } else {
            console.error("Failed to create custom task");
            data.taskId = selectedTab === "dm_other_work" ? undefined : "custom";
            data.value = titleToUse;
          }
        } catch (err) {
          console.error("Error creating custom task:", err);
          data.taskId = selectedTab === "dm_other_work" ? undefined : "custom";
          data.value = selectedTab === "dm_other_work" ? activityValue : (selectedTab === "hr_sales_work" ? activityValue : customTaskName);
        } finally {
          setIsLoading(false);
        }
      } else {
        data.taskId = taskId;
        const selectedTask = tasks.find(t => t.id === taskId);
        if (selectedTask) {
          data.value = selectedTask.projectName ? `${selectedTask.title} (${selectedTask.projectName})` : selectedTask.title;
        }
      }
    } else if (type === "Other") {
      data.subtype = subtype;
      data.value = activityValue;
    } else if (type === "Research") {
      data.value = activityValue;
    }
    
    if ((taskId !== "custom" && !isNewWorkTask) || (data.taskId !== "custom" && data.taskId !== undefined)) {
      onConfirm(data);
    } else {
      // Fallback if creating failed, still punch in
      onConfirm(data);
    }
  };

  const isValid = () => {
    if (selectedTab === "today_work" || selectedTab === "upcoming_work" || selectedTab === "assigned_brands" || selectedTab === "pending_task") {
      if (taskId === "custom") return !!customTaskName.trim();
      return !!taskId;
    }
    if (selectedTab === "hr_sales_work") {
      return !!activityValue;
    }
    if (selectedTab === "dm_other_work") {
      if (isNewWorkTask) return !!activityValue;
      return !!taskId;
    }
    if (selectedTab === "research") {
      return !!activityValue;
    }
    if (selectedTab.startsWith("other_")) {
      return !!activityValue;
    }
    return false;
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

  const todayDate = new Date();
  todayDate.setHours(0, 0, 0, 0);

  const todayTasks = tasks.filter(t => {
    const taskDate = t.dueDate || t.postingDate || t.moduleDeadline;
    if (!taskDate) return true;
    const dateObj = parseLocalDate(taskDate);
    return dateObj <= todayDate;
  }).sort((a, b) => {
    const dateA = a.dueDate || a.postingDate || a.moduleDeadline ? parseLocalDate(a.dueDate || a.postingDate || a.moduleDeadline) : new Date(0);
    const dateB = b.dueDate || b.postingDate || b.moduleDeadline ? parseLocalDate(b.dueDate || b.postingDate || b.moduleDeadline) : new Date(0);
    return dateA.getTime() - dateB.getTime();
  });
  
  const upcomingTasks = tasks.filter(t => {
    const taskDate = t.dueDate || t.postingDate || t.moduleDeadline;
    if (!taskDate) return false;
    const dateObj = parseLocalDate(taskDate);
    return dateObj > todayDate;
  }).sort((a, b) => {
    const dateA = parseLocalDate(a.dueDate || a.postingDate || a.moduleDeadline);
    const dateB = parseLocalDate(b.dueDate || b.postingDate || b.moduleDeadline);
    return dateA.getTime() - dateB.getTime();
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[750px] w-[95vw]">
        <DialogHeader>
          <DialogTitle>{isUpdateMode ? "Update Activity" : "Punch In Activity"}</DialogTitle>
          <DialogDescription>
            What will you be working on right now?
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex justify-center items-center py-8">
            <Loader2 className="w-8 h-8 animate-spin text-brand-teal" />
          </div>
        ) : (
          <div className="space-y-6 py-4">
            <Tabs value={selectedTab} onValueChange={(val) => {
              setSelectedTab(val);
              setTaskId("");
              setActivityValue("");
              setIsNewResearch(false);
              setIsNewWorkTask(false);
            }} className="w-full">
              <TabsList className="w-full flex flex-wrap h-auto gap-1 p-1 bg-muted/50 rounded-lg justify-start">
                {!userDept.includes('sales') && !['digital marketing', 'dm'].includes(userDept) && (
                  <>
                    <TabsTrigger value="today_work" className="data-[state=active]:bg-brand-teal data-[state=active]:text-white">Today's Work</TabsTrigger>
                    <TabsTrigger value="upcoming_work" className="data-[state=active]:bg-brand-teal data-[state=active]:text-white">Upcoming Work</TabsTrigger>
                    {(userDept === 'creative' || isHR) && (
                      <TabsTrigger value="pending_task" className="data-[state=active]:bg-brand-teal data-[state=active]:text-white">Pending Tasks</TabsTrigger>
                    )}
                  </>
                )}
                {['digital marketing', 'dm'].includes(userDept) && (
                  <>
                    <TabsTrigger value="assigned_brands" className="data-[state=active]:bg-brand-teal data-[state=active]:text-white">Projects</TabsTrigger>
                    <TabsTrigger value="dm_other_work" className="data-[state=active]:bg-brand-teal data-[state=active]:text-white">Work</TabsTrigger>
                  </>
                )}
                {userDept.includes('sales') && (
                  <TabsTrigger value="hr_sales_work" className="data-[state=active]:bg-brand-teal data-[state=active]:text-white">Work</TabsTrigger>
                )}
                <TabsTrigger value="research" className="data-[state=active]:bg-brand-teal data-[state=active]:text-white">Research</TabsTrigger>
                {(settings?.otherCategories || ["Activity", "Meeting"]).map((cat: string) => (
                  <TabsTrigger key={`other_${cat}`} value={`other_${cat}`} className="data-[state=active]:bg-brand-teal data-[state=active]:text-white">{cat}</TabsTrigger>
                ))}
              </TabsList>

              <div className="mt-6">
                {(selectedTab === "today_work" || selectedTab === "upcoming_work" || selectedTab === "assigned_brands" || selectedTab === "pending_task") && (
                  <div className="space-y-3">
                    <Label className="text-base">{selectedTab === "assigned_brands" ? 'Select Brand' : 'Select Task'}</Label>
                    <div className="max-h-[500px] overflow-y-scroll flex flex-col gap-1.5 pr-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-slate-100/50 [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-thumb]:bg-brand-teal/30 [&::-webkit-scrollbar-thumb]:rounded-full hover:[&::-webkit-scrollbar-thumb]:bg-brand-teal/50 transition-colors" style={{ scrollbarWidth: 'thin', scrollbarColor: '#09A08A4D transparent' }}>
                      {(() => {
                        let activeTasks = [];
                        if (selectedTab === "assigned_brands") {
                          activeTasks = tasks.filter(t => !t.isDmOtherWork); // All active projects are shown regardless of date
                        } else if (selectedTab === "pending_task") {
                          activeTasks = tasks;
                        } else {
                          activeTasks = selectedTab === "today_work" ? todayTasks : upcomingTasks;
                        }
                        
                        const elements = [];
                        
                        if (activeTasks.length === 0) {
                          elements.push(
                            <div key="empty" className="col-span-full py-8 text-center text-muted-foreground bg-muted/20 rounded-lg border border-dashed">
                              No {selectedTab === "assigned_brands" ? "assigned brands" : selectedTab === "today_work" ? "tasks for today" : selectedTab === "pending_task" ? "pending tasks" : "upcoming tasks"}
                            </div>
                          );
                        } else {
                          elements.push(...activeTasks.map(t => (
                            <div 
                              key={t.id} 
                              onClick={() => setTaskId(t.id)}
                              className={`px-3 py-1.5 rounded-lg cursor-pointer border transition-all duration-200 flex items-center justify-between min-h-[38px] ${
                                taskId === t.id 
                                  ? 'border-brand-teal bg-brand-teal/10 shadow-sm ring-1 ring-brand-teal' 
                                  : 'border-border/50 hover:border-brand-teal/50 hover:bg-muted/30'
                              }`}
                            >
                              <div className="font-medium text-sm flex-1 flex items-center gap-2 min-w-0">
                                <span className="whitespace-normal break-words" title={t.title}>{t.title}</span>
                                {t.dueDate && (
                                  <span className="text-[10px] font-semibold bg-rose-50 text-rose-600 px-1.5 py-0.5 rounded shrink-0 whitespace-nowrap">
                                    {typeof t.dueDate === 'string' && t.dueDate.includes('T') ? t.dueDate.split('T')[0] : String(t.dueDate)}
                                  </span>
                                )}
                              </div>
                              {t.projectName && <div className="text-[11px] text-muted-foreground ml-3 bg-muted/40 px-1.5 py-0.5 rounded flex-shrink-0 max-w-[35%] line-clamp-1">{t.projectName}</div>}
                            </div>
                          )));
                        }
                        
                        if (selectedTab === "today_work" && (userDept === 'creative' || userDept === 'development')) {
                          elements.push(
                            <div 
                              key="custom" 
                              onClick={() => setTaskId('custom')}
                              className={`px-3 py-1.5 mt-2 rounded-lg cursor-pointer border transition-all duration-200 flex items-center justify-between min-h-[38px] border-dashed ${
                                taskId === 'custom' 
                                  ? 'border-brand-teal bg-brand-teal/10 shadow-sm ring-1 ring-brand-teal' 
                                  : 'border-slate-300 hover:border-brand-teal/50 hover:bg-muted/30'
                              }`}
                            >
                              <div className="font-medium text-sm line-clamp-1 flex-1 flex items-center gap-2 text-brand-teal">
                                <span>+ Add Custom Work (Not Listed)</span>
                              </div>
                            </div>
                          );
                        }
                        
                        return elements;
                      })()}
                    </div>
                    {taskId === "custom" && (
                      <div className="space-y-2 mt-4 animate-in fade-in zoom-in duration-200">
                        <Label>Custom Task Name</Label>
                        <Input 
                          placeholder="Enter task name (e.g., Client meeting, Quick revision)" 
                          value={customTaskName}
                          onChange={e => setCustomTaskName(e.target.value)}
                        />
                      </div>
                    )}
                  </div>
                )}

                {selectedTab === "hr_sales_work" && (
                  <div className="space-y-3">
                    <Label className="text-base">Select Task</Label>
                    <div className="max-h-[500px] overflow-y-scroll flex flex-col gap-1.5 pr-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-slate-100/50 [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-thumb]:bg-brand-teal/30 [&::-webkit-scrollbar-thumb]:rounded-full hover:[&::-webkit-scrollbar-thumb]:bg-brand-teal/50 transition-colors" style={{ scrollbarWidth: 'thin', scrollbarColor: '#09A08A4D transparent' }}>
                      {/* Show ALL assigned tasks since Sales only has one Work tab (no Today/Upcoming split) */}
                      {tasks.length > 0 && tasks.map(t => (
                        <div 
                          key={t.id} 
                          onClick={() => {
                            setIsNewWorkTask(false);
                            setTaskId(t.id);
                            setActivityValue(t.title);
                          }}
                          className={`px-3 py-1.5 rounded-lg cursor-pointer border transition-all duration-200 flex items-center justify-between min-h-[38px] ${
                            taskId === t.id && !isNewWorkTask
                              ? 'border-brand-teal bg-brand-teal/10 shadow-sm ring-1 ring-brand-teal' 
                              : 'border-border/50 hover:border-brand-teal/50 hover:bg-muted/30'
                          }`}
                        >
                          <div className="font-medium text-sm flex-1 flex items-center gap-2 min-w-0">
                            <span className="whitespace-normal break-words" title={t.title}>{t.title}</span>
                            {t.dueDate && (
                              <span className="text-[10px] font-semibold bg-rose-50 text-rose-600 px-1.5 py-0.5 rounded shrink-0 whitespace-nowrap">
                                {typeof t.dueDate === 'string' && t.dueDate.includes('T') ? t.dueDate.split('T')[0] : String(t.dueDate)}
                              </span>
                            )}
                          </div>
                          {t.projectName && <div className="text-[11px] text-muted-foreground ml-3 bg-muted/40 px-1.5 py-0.5 rounded flex-shrink-0 max-w-[35%] line-clamp-1">{t.projectName}</div>}
                        </div>
                      ))}

                      {/* Show past work tasks that aren't already in the assigned tasks list */}
                      {(() => {
                        const taskTitles = new Set(tasks.map(t => t.title));
                        const filteredPastWork = pastWorkTasks.filter(topic => !taskTitles.has(topic));
                        if (filteredPastWork.length === 0) return null;
                        return filteredPastWork.map(topic => (
                          <div 
                            key={`past_${topic}`} 
                            onClick={() => {
                              setIsNewWorkTask(false);
                              setTaskId("");
                              setActivityValue(topic);
                            }}
                            className={`px-3 py-1.5 rounded-lg cursor-pointer border transition-all duration-200 flex items-center justify-between min-h-[38px] ${
                              activityValue === topic && !taskId && !isNewWorkTask
                                ? 'border-brand-teal bg-brand-teal/10 shadow-sm ring-1 ring-brand-teal' 
                                : 'border-border/50 hover:border-brand-teal/50 hover:bg-muted/30'
                            }`}
                          >
                            <div className="font-medium text-sm flex-1 flex items-center gap-2 min-w-0">
                              <span className="whitespace-normal break-words" title={topic}>{topic}</span>
                            </div>
                          </div>
                        ));
                      })()}
                      
                      <div 
                        key="custom_work" 
                        onClick={() => {
                          setIsNewWorkTask(true);
                          setTaskId("");
                          setActivityValue("");
                        }}
                        className={`px-3 py-1.5 mt-2 rounded-lg cursor-pointer border transition-all duration-200 flex items-center justify-between min-h-[38px] border-dashed ${
                          isNewWorkTask 
                            ? 'border-brand-teal bg-brand-teal/10 shadow-sm ring-1 ring-brand-teal' 
                            : 'border-slate-300 hover:border-brand-teal/50 hover:bg-muted/30'
                        }`}
                      >
                        <div className="font-medium text-sm line-clamp-1 flex-1 flex items-center gap-2 text-brand-teal">
                          <span>+ Add New Work Task</span>
                        </div>
                      </div>
                    </div>
                    
                    {isNewWorkTask && (
                      <div className="space-y-2 mt-4 animate-in fade-in zoom-in duration-200">
                        <Label>New Task Name</Label>
                        <Input 
                          placeholder="Enter new work task..." 
                          value={activityValue}
                          onChange={e => setActivityValue(e.target.value)}
                          autoFocus
                        />
                      </div>
                    )}
                  </div>
                )}

                {selectedTab === "dm_other_work" && (
                  <div className="space-y-3">
                    <Label className="text-base">Select Work Task</Label>
                    <div className="max-h-[500px] overflow-y-scroll flex flex-col gap-1.5 pr-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-slate-100/50 [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-thumb]:bg-brand-teal/30 [&::-webkit-scrollbar-thumb]:rounded-full hover:[&::-webkit-scrollbar-thumb]:bg-brand-teal/50 transition-colors" style={{ scrollbarWidth: 'thin', scrollbarColor: '#09A08A4D transparent' }}>
                      {tasks.filter(t => t.isDmOtherWork).map(t => (
                        <div 
                          key={t.id} 
                          onClick={() => {
                            setIsNewWorkTask(false);
                            setTaskId(t.id);
                          }}
                          className={`px-3 py-1.5 rounded-lg cursor-pointer border transition-all duration-200 flex items-center justify-between min-h-[38px] ${
                            taskId === t.id && !isNewWorkTask
                              ? 'border-brand-teal bg-brand-teal/10 shadow-sm ring-1 ring-brand-teal' 
                              : 'border-border/50 hover:border-brand-teal/50 hover:bg-muted/30'
                          }`}
                        >
                          <div className="font-medium text-sm flex-1 flex items-center gap-2 min-w-0">
                            <span className="whitespace-normal break-words" title={t.title}>{t.title}</span>
                          </div>
                        </div>
                      ))}
                      {tasks.filter(t => t.isDmOtherWork).length === 0 && (
                        <div className="col-span-full py-4 text-center text-sm text-muted-foreground bg-muted/20 rounded-lg border border-dashed">
                          No pending work tasks
                        </div>
                      )}
                      
                      <div 
                        key="custom_work" 
                        onClick={() => {
                          setIsNewWorkTask(true);
                          setTaskId("");
                          setActivityValue("");
                        }}
                        className={`px-3 py-1.5 mt-2 rounded-lg cursor-pointer border transition-all duration-200 flex items-center justify-between min-h-[38px] border-dashed ${
                          isNewWorkTask 
                            ? 'border-brand-teal bg-brand-teal/10 shadow-sm ring-1 ring-brand-teal' 
                            : 'border-slate-300 hover:border-brand-teal/50 hover:bg-muted/30'
                        }`}
                      >
                        <div className="font-medium text-sm line-clamp-1 flex-1 flex items-center gap-2 text-brand-teal">
                          <span>+ Add New Work Task</span>
                        </div>
                      </div>
                    </div>
                    
                    {isNewWorkTask && (
                      <div className="space-y-2 mt-4 animate-in fade-in zoom-in duration-200">
                        <Label>New Task Name</Label>
                        <Input 
                          placeholder="Enter new work task..." 
                          value={activityValue}
                          onChange={e => setActivityValue(e.target.value)}
                          autoFocus
                        />
                      </div>
                    )}
                  </div>
                )}

                {selectedTab === "research" && (
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label>Research Topic</Label>
                      {pastResearch.length > 0 && !isNewResearch ? (
                        <div className="flex gap-2">
                          <Select value={activityValue} onValueChange={(val) => {
                            if (val === "ADD_NEW_RESEARCH_TOPIC") {
                              setIsNewResearch(true);
                              setActivityValue("");
                            } else {
                              setActivityValue(val);
                            }
                          }}>
                            <SelectTrigger className="flex-1">
                              <SelectValue placeholder="Select previous research topic" />
                            </SelectTrigger>
                            <SelectContent>
                              {pastResearch.map(topic => (
                                <SelectItem key={topic} value={topic}>{topic}</SelectItem>
                              ))}
                              <SelectSeparator />
                              <SelectItem value="ADD_NEW_RESEARCH_TOPIC" className="text-brand-teal font-medium">
                                + Add New Research Topic
                              </SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      ) : (
                        <div className="space-y-2">
                          <div className="flex gap-2">
                            <Input 
                              placeholder="Enter new research topic..." 
                              value={activityValue}
                              onChange={(e) => setActivityValue(e.target.value)}
                              className="flex-1"
                              autoFocus
                            />
                            {pastResearch.length > 0 && (
                              <Button 
                                variant="outline" 
                                onClick={() => {
                                  setIsNewResearch(false);
                                  setActivityValue("");
                                }}
                              >
                                Cancel
                              </Button>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {selectedTab.startsWith("other_") && (
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label>{selectedTab.replace("other_", "")} Details</Label>
                      <Input 
                        placeholder={`Enter ${selectedTab.replace("other_", "").toLowerCase()} description...`} 
                        value={activityValue}
                        onChange={(e) => setActivityValue(e.target.value)}
                        className="w-full"
                      />
                    </div>
                  </div>
                )}
              </div>
            </Tabs>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button 
            className="bg-brand-teal hover:bg-brand-teal-light text-white" 
            disabled={!isValid() || isLoading}
            onClick={handleConfirm}
          >
            {isUpdateMode ? "Save" : "Confirm Punch In"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
