"use client";

import React, { useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { API_URL } from "@/lib/config";
import { toast } from "sonner";
import { useConfirm } from "@/context/ConfirmContext";
import { Loader2, Plus, Trash2, Save, X, Check, Maximize, Minimize, Settings2, Download, History, Calendar as CalendarIcon, ChevronDownIcon, Copy, Star, Lightbulb } from "lucide-react";
import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { Badge } from "@/components/ui/badge";
import { useUserContext } from "@/context/UserContext";
import { MultiSelect } from "@/components/ui/multi-select";
import dayjs from "dayjs";

interface ContentCalendarTableProps {
  clientId: string;
  projectId?: string;
  projectName?: string;
  clientName?: string;
  isOldestProject?: boolean;
}

export function ContentCalendarTable({ clientId, clientName, projectId, projectName, isOldestProject }: ContentCalendarTableProps) {
  const searchParams = useSearchParams();
  const highlightTask = searchParams.get('highlightTask');
  const [entries, setEntries] = useState<any[]>([]);
  const [employees, setEmployees] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState(() => {
    const highlightDate = searchParams.get('highlightDate');
    if (highlightDate) {
      const d = new Date(highlightDate);
      if (!isNaN(d.getTime())) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      }
    }
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  });
  const [typeFilter, setTypeFilter] = useState<string>("all");

  const filteredEntries = React.useMemo(() => {
    if (typeFilter === "all") return entries;
    return entries.filter(e => e.postReel === typeFilter);
  }, [entries, typeFilter]);
  const monthYear = selectedDate ? selectedDate.substring(0, 7) : (() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  })();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<any>({});
  const [isSaving, setIsSaving] = useState(false);
  const [isFullScreen, setIsFullScreen] = useState(false);
  const [explanationMode, setExplanationMode] = useState(false);
  const { confirm } = useConfirm();
  const { user } = useUserContext();

  const [companyName, setCompanyName] = useState(clientName || "Harikrushn Digiverse LLP");

  useEffect(() => {
    if (clientName) {
      setCompanyName(clientName);
    } else if (clientId) {
      fetch(`${API_URL}/clients/${clientId}`)
        .then(res => {
          if (res.ok) return res.json();
          throw new Error("Failed to fetch client");
        })
        .then(data => {
          if (data && data.companyName) {
            setCompanyName(data.companyName);
          }
        })
        .catch(err => console.error(err));
    }
  }, [clientId, clientName]);

  const currentMonthDate = React.useMemo(() => {
    if (!monthYear) return new Date();
    const [year, month] = monthYear.split('-');
    return new Date(parseInt(year), parseInt(month) - 1, 1);
  }, [monthYear]);

  const [holidays, setHolidays] = useState<any[]>([]);
  const [settings, setSettings] = useState<any>({
    scriptDateOffset: 14,
    shootDateOffset: 12,
    editingStartOffset: 6,
    approvalOffset: 5
  });
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settingsForm, setSettingsForm] = useState<any>({});

  const [isPdfDialogOpen, setIsPdfDialogOpen] = useState(false);
  const [logsDialogOpen, setLogsDialogOpen] = useState(false);
  const [currentLogs, setCurrentLogs] = useState<any[]>([]);
  const [isCommonLogs, setIsCommonLogs] = useState(false);

  const [selectedDates, setSelectedDates] = useState<Date[] | undefined>([]);
  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);

  const [showReasonDialog, setShowReasonDialog] = useState(false);
  const [pendingStatusChange, setPendingStatusChange] = useState<string | null>(null);
  const [statusChangeReason, setStatusChangeReason] = useState("");
  const [overallMaxDate, setOverallMaxDate] = useState<Date | null>(null);
  const [downloadStartDate, setDownloadStartDate] = useState("");
  const [downloadEndDate, setDownloadEndDate] = useState("");
  const [isDownloadingPdf, setIsDownloadingPdf] = useState(false);

  const fetchOverallMaxDate = async () => {
    try {
      const res = await fetch(`${API_URL}/content-calendar?clientId=${clientId}${projectId ? `&projectId=${projectId}` : ''}`);
      if (res.ok) {
        const data = await res.json();
        let max = new Date(0);
        let found = false;
        data.forEach((e: any) => {
          if (e.postingDate) {
            const d = new Date(e.postingDate);
            if (!isNaN(d.getTime()) && d > max) {
              max = d;
              found = true;
            }
          }
        });
        setOverallMaxDate(found ? max : null);
      }
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    if (clientId) {
      fetchOverallMaxDate();
    }
  }, [clientId, entries]);

  const handleOpenLogs = (entry: any) => {
    setCurrentLogs(entry.logs || []);
    setIsCommonLogs(false);
    setLogsDialogOpen(true);
  };

  const handleOpenCommonLogs = () => {
    const allLogs = entries.flatMap(entry => {
      if (!entry.logs || !Array.isArray(entry.logs)) return [];
      return entry.logs.map((log: any) => ({
        ...log,
        rowConcept: entry.concept || entry.topic || entry.postingDate || "Unknown Row"
      }));
    });
    
    allLogs.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
    
    setCurrentLogs(allLogs);
    setIsCommonLogs(true);
    setLogsDialogOpen(true);
  };
  const tableHeaders = [
    "Posting Date", "Posting Day", "Post/Reel", "Topic", "Concept", "Reference", "Brand Person",
    "Script Date", "Script Link", "Shoot Date", "Shoot Link", "Editing Start",
    "Final Reel Link", "Final Post Link", "Approval by Het", "Is Approved", "Thumbnail Date", "Thumbnail Link",
    "Caption Date", "Caption", "Posting Link IG", "Actual Posting Date", "Remark", ""
  ];

  const fieldKeys = [
    "postingDate", "postingDay", "postReel", "topic", "concept", "reference", "assignedBrandPersonIds",
    "scriptDate", "scriptLink", "shootDate", "shootLink", "editingStart",
    "finalReelLink", "finalPostLink", "approval", "isApproved", "thumbnailDate", "thumbnailLink",
    "captionDate", "caption", "postingLinkOfIg", "actualPostingDate", "remark"
  ];
  
  const [selectedColumnsForPdf, setSelectedColumnsForPdf] = useState<string[]>(tableHeaders.filter(h => h !== ""));

  useEffect(() => {
    // Fetch all holidays once
    fetch(`${API_URL}/holidays`)
      .then(res => res.json())
      .then(data => setHolidays(data || []))
      .catch(err => console.error("Failed to fetch holidays", err));

    fetch(`${API_URL}/employees`)
      .then(res => res.json())
      .then(data => setEmployees(data || []))
      .catch(err => console.error("Failed to fetch employees", err));
  }, []);

  const fetchSettings = async () => {
    try {
      let globalSettings = {
        scriptDateOffset: undefined,
        shootDateOffset: undefined,
        editingStartOffset: undefined,
        approvalOffset: undefined
      };

      const sysRes = await fetch(`${API_URL}/system-settings`);
      if (sysRes.ok) {
        const sysData = await sysRes.json();
        if (sysData) {
          globalSettings = {
            scriptDateOffset: sysData.defaultScriptDateOffset,
            shootDateOffset: sysData.defaultShootDateOffset,
            editingStartOffset: sysData.defaultEditingStartOffset,
            approvalOffset: sysData.defaultApprovalOffset
          };
        }
      }

      const res = await fetch(`${API_URL}/content-calendar-settings?clientId=${clientId}&monthYear=${monthYear}${projectId ? `&projectId=${projectId}` : ''}`);
      let customSettings = {};
      if (res.ok) {
        const data = await res.json();
        if (data && Object.keys(data).length > 0) {
          // Temporarily ignore cached backend defaults (14, 12, 6, 5) if they match exactly
          if (data.scriptDateOffset == 14 && data.shootDateOffset == 12 && data.editingStartOffset == 6 && data.approvalOffset == 5) {
             delete data.scriptDateOffset;
             delete data.shootDateOffset;
             delete data.editingStartOffset;
             delete data.approvalOffset;
          }
          customSettings = data;
        }
      }

      const finalSettings = {
        ...globalSettings,
        ...customSettings
      };

      setSettings(finalSettings);
      setSettingsForm(finalSettings);
    } catch (err) {
      console.error("Failed to fetch settings", err);
    }
  };

  const fetchEntries = async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`${API_URL}/content-calendar?clientId=${clientId}&monthYear=${monthYear}`);
      if (res.ok) {
        let data = await res.json();
        if (projectId) {
           data = data.filter((entry: any) => entry.projectId === projectId || (isOldestProject && !entry.projectId));
        }
        data.sort((a: any, b: any) => {
          if (!a.postingDate) return 1;
          if (!b.postingDate) return -1;
          return a.postingDate.localeCompare(b.postingDate);
        });
        setEntries(data);
      }
    } catch (error) {
      console.error("Failed to fetch calendar entries", error);
      toast.error("Failed to load content calendar");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (highlightTask && clientId) {
      fetch(`${API_URL}/content-calendar?clientId=${clientId}`)
        .then(res => {
          if (res.ok) return res.json();
          throw new Error("Failed to fetch entries");
        })
        .then(data => {
          if (Array.isArray(data)) {
            if (projectId) {
               data = data.filter((entry: any) => entry.projectId === projectId || (isOldestProject && !entry.projectId));
            }
            const task = data.find((t: any) => t.id === highlightTask);
            if (task) {
              const dateVal = task.postingDate || task.scriptDate || task.shootDate || task.editingStart || task.actualPostingDate || task.monthYear;
              if (dateVal && typeof dateVal === 'string') {
                if (dateVal.match(/^\d{4}-\d{2}-\d{2}/)) {
                  setSelectedDate(dateVal);
                } else if (dateVal.match(/^\d{4}-\d{2}/)) {
                  setSelectedDate(`${dateVal}-01`);
                }
              }
            }
          }
        })
        .catch(err => console.error(err));
    }
  }, [highlightTask, clientId, projectId]);

  useEffect(() => {
    fetchEntries();
    fetchSettings();
  }, [clientId, monthYear, projectId]);

  useEffect(() => {
    if (entries.length > 0 && highlightTask) {
      setTimeout(() => {
        const el = document.getElementById(`task-${highlightTask}`);
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("!bg-amber-200");
          setTimeout(() => {
            el.classList.remove("!bg-amber-200");
          }, 4000);
        }
      }, 500);
    }
  }, [entries, highlightTask]);

  useEffect(() => {
    if (isFullScreen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "auto";
    }
    return () => {
      document.body.style.overflow = "auto";
    };
  }, [isFullScreen]);

  const handleAddRow = async () => {
    try {
      const storedUser = localStorage.getItem('user');
      const user = storedUser ? JSON.parse(storedUser) : null;
      const userName = user?.name || (user?.firstName ? `${user.firstName} ${user.lastName || ''}`.trim() : null) || "Unknown User";

      const res = await fetch(`${API_URL}/content-calendar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clientId,
          projectId,
          monthYear,
          updatedBy: userName,
          postReel: typeFilter !== "all" ? typeFilter : undefined,
          ...(typeFilter === "Story" ? {
            scriptLink: '-',
            shootLink: '-',
            thumbnailLink: '-',
            caption: '-',
            postingLinkOfIg: '-',
            finalPostLink: '-',
            scriptDate: '-',
            shootDate: '-',
            thumbnailDate: '-',
            captionDate: '-',
            assignedBrandPersonIds: []
          } : {})
        }),
      });
      if (res.ok) {
        const newEntry = await res.json();
        setEntries([...entries, newEntry]);
        startEditing(newEntry, true);
      }
    } catch (error) {
      toast.error("Failed to add new row");
    }
  };

  const handleAddRowWithDate = async (dateString: string) => {
    try {
      const storedUser = localStorage.getItem('user');
      const user = storedUser ? JSON.parse(storedUser) : null;
      const userName = user?.name || (user?.firstName ? `${user.firstName} ${user.lastName || ''}`.trim() : null) || "Unknown User";

      let payload: any = {
        clientId,
        projectId,
        monthYear,
        postingDate: dateString,
        updatedBy: userName,
        postReel: typeFilter !== "all" ? typeFilter : undefined,
        ...(typeFilter === "Story" ? {
          scriptLink: '-',
          shootLink: '-',
          thumbnailLink: '-',
          caption: '-',
          postingLinkOfIg: '-',
          finalPostLink: '-',
          scriptDate: '-',
          shootDate: '-',
          thumbnailDate: '-',
          captionDate: '-',
          assignedBrandPersonIds: []
        } : {})
      };

      try {
        let d: Date | undefined;
        if (dateString.includes("-")) {
          d = new Date(dateString);
        } else if (dateString.includes("/")) {
          const parts = dateString.split("/");
          d = new Date(`${parts[2]}-${parts[1]}-${parts[0]}`);
        }
        if (d && !isNaN(d.getTime())) {
          payload.postingDay = d.toLocaleDateString("en-US", { weekday: "long" });

          const isHoliday = (date: Date) => {
            const dStr = date.toISOString().split('T')[0];
            return holidays.some(h => h.date && h.date.startsWith(dStr));
          };

          const subtractDays = (startDate: Date, workingDaysToSubtract: number) => {
            let currentDate = new Date(startDate);
            let daysSubtracted = 0;
            while (daysSubtracted < workingDaysToSubtract) {
              currentDate.setDate(currentDate.getDate() - 1);
              const dayOfWeek = currentDate.getDay();
              if (dayOfWeek !== 0 && !isHoliday(currentDate)) {
                daysSubtracted++;
              }
            }
            return currentDate.toISOString().split('T')[0];
          };

          const parseOffset = (val: any, defaultVal: number) => {
            const num = Number(val);
            return (!isNaN(num) && num > 0) ? num : defaultVal;
          };

          const scriptOffset = parseOffset(settings?.scriptDateOffset, 14);
          const shootOffset = parseOffset(settings?.shootDateOffset, 12);
          const editingOffset = parseOffset(settings?.editingStartOffset, 6);
          const approvalOffset = parseOffset(settings?.approvalOffset, 5);

          payload.scriptDate = subtractDays(d, scriptOffset);
          payload.shootDate = subtractDays(d, shootOffset);
          payload.editingStart = subtractDays(d, editingOffset);
          payload.captionDate = payload.editingStart;
          payload.thumbnailDate = payload.editingStart;
          payload.approval = subtractDays(d, approvalOffset);
        }
      } catch (e) {
        console.error("Error parsing date", e);
      }

      const res = await fetch(`${API_URL}/content-calendar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const newEntry = await res.json();
        setEntries(prev => [...prev, newEntry]);
      }
    } catch (error) {
      toast.error("Failed to add new row");
    }
  };

  useEffect(() => {
    if (entries && entries.length > 0) {
      const existingDates = entries
        .filter(e => e.postingDate)
        .map(e => {
          const [y, m, d] = e.postingDate.split('-');
          return new Date(parseInt(y), parseInt(m) - 1, parseInt(d));
        });
      setSelectedDates(existingDates);
    } else {
      setSelectedDates([]);
    }
  }, [entries]);

  const handleSyncDates = async () => {
    setIsDatePickerOpen(false); // Close popover immediately
    
    let addedCount = 0;
    let deletedCount = 0;

    const selectedStrings = (selectedDates || []).map(date => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    });

    // 1. Delete rows that were unselected
    for (const entry of entries) {
      if (entry.postingDate && !selectedStrings.includes(entry.postingDate)) {
        try {
          await fetch(`${API_URL}/content-calendar/${entry.id}`, { method: "DELETE" });
          deletedCount++;
        } catch (error) {
          console.error("Failed to delete", error);
        }
      }
    }

    // 2. Add rows that are newly selected
    for (const dateString of selectedStrings) {
      const exists = entries.some(e => e.postingDate === dateString);
      if (!exists) {
        await handleAddRowWithDate(dateString);
        addedCount++;
      }
    }
    
    if (addedCount > 0 && deletedCount > 0) {
      toast.success(`Added ${addedCount} row(s) and deleted ${deletedCount} row(s)`);
    } else if (addedCount > 0) {
      toast.success(`Added ${addedCount} row(s)`);
    } else if (deletedCount > 0) {
      toast.success(`Deleted ${deletedCount} row(s)`);
    }
    
    if (addedCount > 0 || deletedCount > 0) {
      fetchEntries();
    }
  };

  const handleStatusChange = async (newStatus: string) => {
    const oldStatus = settings?.approvalStatus || "Pending";
    if (
      (oldStatus === "Approved by Client" && newStatus !== "Approved by Client") ||
      newStatus === "Rejected" ||
      newStatus === "Changes Requested"
    ) {
      setPendingStatusChange(newStatus);
      setStatusChangeReason("");
      setShowReasonDialog(true);
      return;
    }
    
    await proceedWithStatusChange(newStatus, "");
  };

  const proceedWithStatusChange = async (newStatus: string, reason: string) => {
    try {
      const newLog = {
        timestamp: new Date().toISOString(),
        status: newStatus,
        user: user?.name || "Unknown User",
        reason: reason
      };
      const updatedLogs = [...(settings?.statusLogs || []), newLog];
      const updatedSettings = { ...settings, clientId, projectId, monthYear, approvalStatus: newStatus, statusLogs: updatedLogs };
      
      const res = await fetch(`${API_URL}/content-calendar-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updatedSettings)
      });

      if (res.ok) {
        const data = await res.json();
        setSettings(data);
        toast.success(`Calendar status updated to ${newStatus}`);
      } else {
        toast.error("Failed to update calendar status");
      }
    } catch (error) {
      toast.error("An error occurred while updating status");
    }
  };

  const [isNewRow, setIsNewRow] = useState(false);

  const startEditing = (entry: any, isNew = false) => {
    setEditingId(entry.id);
    setEditForm({ ...entry });
    setIsNewRow(isNew);
  };

  const cancelEditing = async () => {
    if (isNewRow && editingId) {
      try {
        await fetch(`${API_URL}/content-calendar/${editingId}`, { method: 'DELETE' });
        setEntries(prev => prev.filter(e => e.id !== editingId));
      } catch (err) {
        console.error("Failed to delete new row on cancel", err);
      }
    }
    setEditingId(null);
    setEditForm({});
    setIsNewRow(false);
  };


  const handleSaveRow = async () => {
    if (!editingId) return;
    setIsSaving(true);
    const storedUser = localStorage.getItem('user');
    const user = storedUser ? JSON.parse(storedUser) : null;
    const userName = user?.name || (user?.firstName ? `${user.firstName} ${user.lastName || ''}`.trim() : null) || "Unknown User";

    const payload = { ...editForm, updatedBy: userName };
    if (payload.postReel === 'Post') {
      payload.shootLink = payload.shootLink || '-';
      payload.thumbnailLink = payload.thumbnailLink || '-';
    } else if (payload.postReel === 'Story') {
      payload.scriptLink = '-';
      payload.shootLink = '-';
      payload.thumbnailLink = '-';
      payload.caption = '-';
      payload.postingLinkOfIg = '-';
      payload.finalPostLink = '-';
      payload.scriptDate = '-';
      payload.shootDate = '-';
      payload.thumbnailDate = '-';
      payload.captionDate = '-';
      payload.assignedBrandPersonIds = [];
    }

    if (payload.postingDate && typeof payload.postingDate === "string") {
      const parts = payload.postingDate.split("-");
      if (parts.length >= 2) {
        payload.monthYear = `${parts[0]}-${parts[1]}`;
      }
    }

    try {
      const res = await fetch(`${API_URL}/content-calendar/${editingId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        toast.success("Row updated");
        setEditingId(null);
        setIsNewRow(false);
        fetchEntries();
      } else {
        toast.error("Failed to update row");
      }
    } catch (error) {
      toast.error("Failed to update row");
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveSettings = async () => {
    try {
      const payload = {
        ...settingsForm,
        clientId,
        projectId,
        monthYear,
        scriptDateOffset: Number(settingsForm.scriptDateOffset),
        shootDateOffset: Number(settingsForm.shootDateOffset),
        editingStartOffset: Number(settingsForm.editingStartOffset),
        approvalOffset: Number(settingsForm.approvalOffset),
      };
      const res = await fetch(`${API_URL}/content-calendar-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        setSettings(data);
        setIsSettingsOpen(false);
        toast.success("Settings saved for this month!");
      } else {
        toast.error("Failed to save settings");
      }
    } catch (error) {
      toast.error("Failed to save settings");
    }
  };

  const handleDeleteRow = async (id: string) => {
    const isConfirmed = await confirm({
      title: "Delete Row",
      message: "Are you sure you want to delete this row? This action cannot be undone.",
      confirmText: "Delete",
    });
    
    if (!isConfirmed) return;
    
    try {
      const res = await fetch(`${API_URL}/content-calendar/${id}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setEntries(entries.filter(e => e.id !== id));
        toast.success("Row deleted");
      }
    } catch (error) {
      toast.error("Failed to delete row");
    }
  };

  const handleToggleHighlight = async (entry: any) => {
    try {
      const currentUserId = user?.id || user?._id;
      if (!currentUserId) {
        toast.error("User session not found");
        return;
      }
      
      const currentList = Array.isArray(entry.highlightedByUserIds) ? entry.highlightedByUserIds : [];
      let newList: string[];
      let isHighlightedNow: boolean;
      
      if (currentList.includes(currentUserId)) {
        newList = currentList.filter((id: string) => id !== currentUserId);
        isHighlightedNow = false;
      } else {
        newList = [...currentList, currentUserId];
        isHighlightedNow = true;
      }
      
      const res = await fetch(`${API_URL}/content-calendar/${entry.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ highlightedByUserIds: newList }),
      });
      if (res.ok) {
        setEntries((prev) =>
          prev.map((e) => (e.id === entry.id ? { ...e, highlightedByUserIds: newList } : e))
        );
        toast.success(isHighlightedNow ? "Row highlighted!" : "Highlight removed!");
      } else {
        toast.error("Failed to update highlight status");
      }
    } catch (err) {
      console.error(err);
      toast.error("Failed to toggle highlight status");
    }
  };

  const handleClearAllHighlights = async () => {
    const currentUserId = user?.id || user?._id;
    if (!currentUserId) {
      toast.error("User session not found");
      return;
    }
    
    const highlightedEntries = entries.filter(e => 
      Array.isArray(e.highlightedByUserIds) && e.highlightedByUserIds.includes(currentUserId)
    );
    
    if (highlightedEntries.length === 0) {
      toast.info("No highlighted rows to clear.");
      return;
    }
    
    
    try {
      setIsSaving(true);
      await Promise.all(highlightedEntries.map(async (entry) => {
        const currentList = Array.isArray(entry.highlightedByUserIds) ? entry.highlightedByUserIds : [];
        const newList = currentList.filter((id: string) => id !== currentUserId);
        
        await fetch(`${API_URL}/content-calendar/${entry.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ highlightedByUserIds: newList }),
        });
      }));
      
      setEntries(prev => prev.map(e => {
        if (Array.isArray(e.highlightedByUserIds) && e.highlightedByUserIds.includes(currentUserId)) {
          return {
            ...e,
            highlightedByUserIds: e.highlightedByUserIds.filter((id: string) => id !== currentUserId)
          };
        }
        return e;
      }));
      
      toast.success("All highlights cleared!");
    } catch (err) {
      console.error(err);
      toast.error("Failed to clear some highlights");
    } finally {
      setIsSaving(false);
    }
  };



  const handleDownloadPdf = async () => {
    if (entries.length === 0) {
      toast.error("No entries to download");
      return;
    }
    
    if (selectedColumnsForPdf.length === 0) {
      toast.error("Please select at least one column");
      return;
    }
    await exportPdf();
  };

  const exportPdf = async () => {
    setIsDownloadingPdf(true);
    try {
      const doc = new jsPDF("landscape");

      try {
        const fontRes = await fetch("/fonts/ArialUnicode.ttf");
        const blob = await fontRes.blob();
        
        const fontBase64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => {
            const result = reader.result as string;
            const base64 = result.split(',')[1];
            resolve(base64);
          };
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        });
        
        doc.addFileToVFS("ArialUnicode.ttf", fontBase64);
        doc.addFont("ArialUnicode.ttf", "ArialUnicode", "normal");
        doc.setFont("ArialUnicode");
      } catch (err) {
        console.error("Failed to load Unicode font", err);
      }
    
    const formatDateToDDMMYY = (dateStr: string) => {
      if (!dateStr) return "";
      const match = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (match) {
        const [_, year, month, day] = match;
        return `${day}-${month}-${year.slice(-2)}`;
      }
      return dateStr;
    };

    const formatMonthYearToMMYY = (myStr: string) => {
      if (!myStr) return "";
      const match = myStr.match(/^(\d{4})-(\d{2})$/);
      if (match) {
        const [_, year, month] = match;
        return `${month}-${year.slice(-2)}`;
      }
      return myStr;
    };

    const columnsToRender = [...selectedColumnsForPdf].sort((a, b) => tableHeaders.indexOf(a) - tableHeaders.indexOf(b));
    const indicesToRender = columnsToRender.map(col => tableHeaders.indexOf(col));

    let entriesToProcess = entries;
    if (downloadStartDate || downloadEndDate) {
      try {
        const res = await fetch(`${API_URL}/content-calendar?clientId=${clientId}${projectId ? `&projectId=${projectId}` : ''}`);
        if (res.ok) {
          entriesToProcess = await res.json();
        }
      } catch (e) {
        console.error("Failed to fetch all entries for date range download", e);
      }
    }

    const filteredEntries = entriesToProcess.filter((entry: any) => {
      const matchesType = typeFilter === "all" || entry.postReel === typeFilter;
      let matchesDate = true;
      if (entry.postingDate) {
        const postingDStr = entry.postingDate.substring(0, 10);
        if (downloadStartDate && postingDStr < downloadStartDate) matchesDate = false;
        if (downloadEndDate && postingDStr > downloadEndDate) matchesDate = false;
      } else if (downloadStartDate || downloadEndDate) {
        matchesDate = false;
      }
      return matchesType && matchesDate;
    });

    if (filteredEntries.length === 0) {
      toast.error("No entries found for the selected filters to download");
      return;
    }

    // Determine the calendar start date's month and year dynamically
    let monthLabel = "";
    let headerMonthStr = formatMonthYearToMMYY(monthYear);

    if (filteredEntries.length > 0 && filteredEntries[0].postingDate) {
      const parts = filteredEntries[0].postingDate.split("-");
      if (parts.length >= 2) {
        const year = parts[0];
        const monthNum = parseInt(parts[1], 10);
        headerMonthStr = `${parts[1]}-${parts[0].slice(-2)}`;
        
        const monthNames = [
          "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"
        ];
        if (monthNum >= 1 && monthNum <= 12) {
          monthLabel = `${monthNames[monthNum - 1]}`;
        }
      }
    }

    if (!monthLabel) {
      const [year, month] = monthYear.split("-");
      const monthNum = parseInt(month, 10);
      const monthNames = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
      ];
      if (monthNum >= 1 && monthNum <= 12) {
        monthLabel = `${monthNames[monthNum - 1]}`;
      } else {
        monthLabel = monthYear;
      }
    }

    const pageWidth = doc.internal.pageSize.width;

    // Corporate Letterhead Style
    // Company Name
    doc.setFontSize(22);
    doc.setTextColor(13, 148, 136); // Brand teal
    doc.setFont("helvetica", "bold");
    doc.text(companyName, 14, 20);

    // Document Title
    doc.setFontSize(14);
    doc.setTextColor(50, 50, 50);
    doc.setFont("helvetica", "normal");
    doc.text("Content Calendar", 14, 28);

    // Right-aligned meta details
    doc.setFontSize(10);
    doc.setTextColor(100, 100, 100);
    doc.setFont("helvetica", "normal");
    doc.text(`Month: ${headerMonthStr}`, pageWidth - 14, 20, { align: "right" });

    // Decorative separator line
    doc.setDrawColor(220, 220, 220);
    doc.setLineWidth(0.5);
    doc.line(14, 38, pageWidth - 14, 38);

    const dateFields = ["postingDate", "scriptDate", "shootDate", "editingStart", "actualPostingDate"];
    const tableData = filteredEntries.map(entry => {
      return indicesToRender.map(idx => {
        const key = fieldKeys[idx];
        const isPostDisabled = entry.postReel === 'Post' && ['shootDate', 'thumbnailDate', 'shootLink', 'thumbnailLink'].includes(key);
        const isStoryDisabled = entry.postReel === 'Story' && ['scriptDate', 'scriptLink', 'shootDate', 'shootLink', 'thumbnailDate', 'thumbnailLink', 'captionDate', 'caption', 'postingLinkOfIg', 'finalPostLink', 'assignedBrandPersonIds'].includes(key);
        if (isPostDisabled || isStoryDisabled) {
          return "-";
        }
        let val: any = entry[key] || "";
        if (dateFields.includes(key)) {
          val = formatDateToDDMMYY(val);
        } else if (key.toLowerCase().includes("link") || key === "reference") {
          const matches = (typeof val === 'string' ? val : '').match(/(https?:\/\/[^\s,;]+|www\.[^\s,;]+)/gi);
          if (matches && matches.length > 0) {
            let url = matches[0];
            if (url.toLowerCase().startsWith("www.")) {
              url = "https://" + url;
            }
            val = {
              content: "           ", // spaces to allocate width
              url: url,
              isButton: true
            };
          }
        }
        return val;
      });
    });

    autoTable(doc, {
      head: [columnsToRender],
      body: tableData,
      startY: 38,
      theme: 'grid',
      styles: { fontSize: 8, cellPadding: 2, overflow: "linebreak", font: "ArialUnicode" },
      headStyles: { fillColor: [13, 148, 136], textColor: 255, fontStyle: 'bold', halign: 'center' },
      alternateRowStyles: { fillColor: [248, 250, 252] },
      margin: { top: 44, right: 14, bottom: 20, left: 14 },
      willDrawCell: (data) => {
        if (data.section === 'body') {
          if (data.cell.raw && typeof data.cell.raw === 'object' && (data.cell.raw as any).url) {
            doc.setTextColor(0, 102, 204); // Blue color for links
          } else {
            const rawValue = String(data.cell.raw || "");
            if (rawValue.match(/(https?:\/\/[^\s]+|www\.[^\s]+)/)) {
              doc.setTextColor(0, 102, 204); // Blue color for links
            }
          }
        }
      },
      didDrawCell: (data) => {
        if (data.section === 'body') {
          if (data.cell.raw && typeof data.cell.raw === 'object' && (data.cell.raw as any).isButton && (data.cell.raw as any).url) {
            const url = (data.cell.raw as any).url;
            
            const btnW = 11;
            const btnH = 5.5;
            const paddingX = (data.cell.width - btnW) / 2 > 0 ? (data.cell.width - btnW) / 2 : 1; 
            const btnX = data.cell.x + paddingX;
            const btnY = data.cell.y + (data.cell.height - btnH) / 2;
            
            // Draw rounded background (light blue)
            doc.setFillColor(239, 246, 255);
            doc.setDrawColor(219, 234, 254);
            doc.setLineWidth(0.2);
            doc.roundedRect(btnX, btnY, btnW, btnH, 1, 1, 'FD');
            
            // Draw text
            doc.setTextColor(37, 99, 235);
            doc.setFont("helvetica", "bold");
            doc.setFontSize(6.5);
            doc.text("Link", btnX + 2.5, btnY + 4);
            
            // reset font state
            doc.setFont("helvetica", "normal");
            doc.setFontSize(8);
            doc.setTextColor(0, 0, 0);

            // Clickable area for "Link" (Opens link directly)
            doc.link(btnX, btnY, btnW, btnH, { url: url });
          } else {
            const rawValue = String(data.cell.raw || "");
            const urlMatch = rawValue.match(/(https?:\/\/[^\s]+|www\.[^\s]+)/);
            if (urlMatch) {
              let url = urlMatch[0];
              if (url.toLowerCase().startsWith('www.')) {
                url = 'https://' + url;
              }
              doc.link(data.cell.x, data.cell.y, data.cell.width, data.cell.height, { url: url });
            }
          }
        }
      }
    });

    // Add Footer with Page Numbers
    const pageCount = (doc as any).internal.getNumberOfPages();
    for (let i = 1; i <= pageCount; i++) {
      doc.setPage(i);
      doc.setFontSize(8);
      doc.setTextColor(150, 150, 150);
      doc.text(`Page ${i} of ${pageCount}  |  ${companyName}`, pageWidth / 2, doc.internal.pageSize.height - 10, { align: "center" });
    }

    const safeCompanyName = companyName.replace(/[\\/:*?"<>|]/g, "");
    doc.save(`${safeCompanyName} ${monthLabel} Content Calendar.pdf`);
    setIsPdfDialogOpen(false);
    } finally {
      setIsDownloadingPdf(false);
    }
  };

  const formatDateDisplay = (dateString: any) => {
    if (!dateString) return null;
    if (typeof dateString === "string" && /^\d{4}-\d{2}-\d{2}$/.test(dateString)) {
      const [year, month, day] = dateString.split("-");
      return `${day}-${month}-${year}`;
    }
    return dateString;
  };

  const handleDaySelect = (dateString: string) => {
    setEditForm((prev: any) => {
      const updates: any = { postingDate: dateString };
      try {
        if (dateString) {
          let d: Date | undefined;
          if (dateString.includes("-")) {
            d = new Date(dateString);
          } else if (dateString.includes("/")) {
            const parts = dateString.split("/");
            d = new Date(`${parts[2]}-${parts[1]}-${parts[0]}`);
          }
          if (d && !isNaN(d.getTime())) {
            // Posting Day
            updates.postingDay = d.toLocaleDateString("en-US", { weekday: "long" });

            // Helper to subtract working days and return YYYY-MM-DD
            const isHoliday = (date: Date) => {
              const dStr = date.toISOString().split('T')[0];
              return holidays.some(h => h.date && h.date.startsWith(dStr));
            };

            const subtractDays = (startDate: Date, workingDaysToSubtract: number) => {
              let currentDate = new Date(startDate);
              let daysSubtracted = 0;
              
              while (daysSubtracted < workingDaysToSubtract) {
                currentDate.setDate(currentDate.getDate() - 1);
                const dayOfWeek = currentDate.getDay(); // 0 is Sunday
                if (dayOfWeek !== 0 && !isHoliday(currentDate)) {
                  daysSubtracted++;
                }
              }
              return currentDate.toISOString().split('T')[0];
            };

            // Calculate dates if they aren't already manually set
            const parseOffset = (val: any, defaultVal: number) => {
              const num = Number(val);
              return (!isNaN(num) && num > 0) ? num : defaultVal;
            };

            const scriptOffset = parseOffset(settings?.scriptDateOffset, 14);
            const shootOffset = parseOffset(settings?.shootDateOffset, 12);
            const editingOffset = parseOffset(settings?.editingStartOffset, 6);
            const approvalOffset = parseOffset(settings?.approvalOffset, 5);

            // ALWAYS recalculate if the user changes the posting date, overriding any existing calculated values
            if (prev.postReel === 'Story' || updates.postReel === 'Story') {
              updates.scriptDate = "-";
              updates.shootDate = "-";
              updates.thumbnailDate = "-";
              updates.captionDate = "-";
            } else {
              updates.scriptDate = subtractDays(d, scriptOffset);
              updates.shootDate = subtractDays(d, shootOffset);
            }
            updates.editingStart = subtractDays(d, editingOffset);
            updates.approval = subtractDays(d, approvalOffset);
          }
        }
      } catch (e) {
        console.error("Error parsing date", e);
      }
      return { ...prev, ...updates };
    });
  };

  const containerClasses = isFullScreen 
    ? "fixed inset-0 z-50 bg-slate-50 flex flex-col" 
    : "bg-white rounded-xl shadow-sm border border-slate-200 flex flex-col mt-6 min-h-[calc(100vh-350px)]";

  const [pickerView, setPickerView] = useState<'days' | 'months'>('days');
  const [pickerYear, setPickerYear] = useState(() => new Date().getFullYear());

  useEffect(() => {
    if (isDatePickerOpen) {
      setPickerView('days');
      setPickerYear(currentMonthDate.getFullYear());
    }
  }, [isDatePickerOpen, currentMonthDate]);

  const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  return (
    <div className={containerClasses}>
      <div className="p-4 border-b border-slate-200 flex flex-wrap items-center justify-between gap-4 bg-white">
        <div>
          <h2 className="text-lg font-bold text-slate-800 flex items-center gap-2">
            Content Calendar
            {projectName && (
              <>
                <span className="text-slate-300">|</span>
                <span className="text-brand-teal text-base">{projectName}</span>
              </>
            )}
          </h2>
          <p className="text-xs text-slate-500">Plan and track content creation and posting</p>
        </div>
        <div className="flex items-start gap-3">
          <div className="flex items-start gap-2">
            <div className="flex flex-col gap-1 items-end">
              <div className="flex items-center gap-1">
              <Select 
                value={settings?.approvalStatus || "Pending"} 
                onValueChange={handleStatusChange}
              >
                <SelectTrigger className={`w-auto h-9 text-xs font-semibold border-none shadow-none focus:ring-0 [&>svg]:hidden px-3 transition-colors rounded-md ${
                  settings?.approvalStatus === "Approved by Client" ? "text-emerald-600 bg-emerald-50 hover:bg-emerald-100" :
                  settings?.approvalStatus === "Changes Requested" ? "text-indigo-600 bg-indigo-50 hover:bg-indigo-100" :
                  settings?.approvalStatus === "Rejected" ? "text-rose-600 bg-rose-50 hover:bg-rose-100" :
                  "text-amber-600 bg-amber-50 hover:bg-amber-100"
                }`}>
                  <SelectValue placeholder="Approval Status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="Pending">Pending</SelectItem>
                  <SelectItem value="Approved by Client">Approved by Client</SelectItem>
                  <SelectItem value="Changes Requested">Changes Requested</SelectItem>
                  <SelectItem value="Rejected">Rejected</SelectItem>
                </SelectContent>
              </Select>
              {settings?.statusLogs && settings.statusLogs.length > 0 && (
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-slate-600">
                      <History className="h-4 w-4" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-80 p-0" align="start">
                    <div className="p-3 border-b border-slate-100 bg-slate-50/50">
                      <h4 className="font-semibold text-sm text-slate-800">Status History</h4>
                    </div>
                    <div className="max-h-60 overflow-y-auto p-2">
                      <div className="flex flex-col gap-2">
                        {[...settings.statusLogs].reverse().map((log: any, i: number) => (
                          <div key={i} className="text-sm p-2 rounded bg-slate-50 border border-slate-100">
                            <div className="flex justify-between items-start mb-1">
                              <span className="font-medium text-slate-700">{log.status}</span>
                              <span className="text-[10px] text-slate-400">{dayjs(log.timestamp).format('DD/MM/YYYY, hh:mm A')}</span>
                            </div>
                            <div className="text-xs text-slate-500">by {log.user}</div>
                            {log.reason && (
                              <div className="mt-1 text-xs text-rose-600 bg-rose-50 p-1.5 rounded border border-rose-100">
                                <strong>Reason:</strong> {log.reason}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>
              )}
            </div>
            {settings?.statusLogs && settings.statusLogs.length > 0 && settings.statusLogs[settings.statusLogs.length - 1].reason && (
              <div className="text-[10px] text-rose-600 font-medium max-w-[200px] truncate bg-rose-50 px-2 py-0.5 rounded border border-rose-100" title={settings.statusLogs[settings.statusLogs.length - 1].reason}>
                Reason: {settings.statusLogs[settings.statusLogs.length - 1].reason}
              </div>
            )}
            </div>
            {overallMaxDate && (
              <div className="flex items-center gap-2 border border-orange-200 bg-orange-50 rounded-md px-3 h-9 text-sm shrink-0 shadow-sm">
                <span className="font-medium text-orange-700">End Date:</span>
                <span className="font-bold text-orange-900">{overallMaxDate.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}</span>
              </div>
            )}
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-[120px] h-9 text-xs bg-white border border-slate-200">
                <SelectValue placeholder="All Types" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Types</SelectItem>
                <SelectItem value="Post">Post</SelectItem>
                <SelectItem value="Reel">Reel</SelectItem>
                <SelectItem value="Story">Story</SelectItem>
              </SelectContent>
            </Select>
            <Popover open={isDatePickerOpen} onOpenChange={setIsDatePickerOpen}>
              <PopoverTrigger asChild>
              <Button variant="outline" className="w-[180px] justify-start text-left font-normal h-9 bg-white">
                <CalendarIcon className="mr-2 h-4 w-4" />
                {currentMonthDate.toLocaleString('default', { month: 'long', year: 'numeric' })}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start" side="bottom">
              {pickerView === 'days' ? (
                <>
                  <div className="flex justify-center pt-3 pb-1 border-b border-slate-100">
                    <Button variant="ghost" size="sm" className="h-8 text-[15px] font-semibold hover:bg-slate-100 text-slate-800" onClick={() => setPickerView('months')}>
                      {currentMonthDate.toLocaleString('default', { month: 'long', year: 'numeric' })}
                      <ChevronDownIcon className="ml-1 h-4 w-4 text-slate-400" />
                    </Button>
                  </div>
                  <div className="p-2 [&_[data-selected=true]]:!bg-brand-teal/15 [&_[data-selected=true]]:!text-brand-teal [&_[data-selected=true]]:!font-bold [&_[data-selected=true]]:!rounded-md [&_[data-selected-single=true]]:!bg-brand-teal/15 [&_[data-selected-single=true]]:!text-brand-teal [&_[data-selected-single=true]]:!font-bold [&_[data-selected-single=true]]:!rounded-md">
                    <Calendar
                      mode="multiple"
                      selected={selectedDates}
                      onSelect={setSelectedDates}
                      month={currentMonthDate}
                      onMonthChange={(date) => {
                        const y = date.getFullYear();
                        const m = String(date.getMonth() + 1).padStart(2, '0');
                        setSelectedDate(`${y}-${m}-01`);
                      }}
                      className="border-0 p-0"
                      classNames={{
                        month_caption: 'hidden', 
                        nav: 'hidden', 
                        head_cell: 'text-slate-400 font-medium text-[0.8rem] w-9',
                        cell: 'text-center text-sm p-0 relative [&:has([aria-selected])]:bg-transparent focus-within:relative focus-within:z-20',
                        day: 'h-9 w-9 p-0 font-normal hover:bg-slate-100 rounded-md transition-colors',
                      }}
                    />
                  </div>
                  <div className="p-3 border-t border-slate-100 bg-slate-50/50 flex justify-between items-center gap-4 rounded-b-md">
                    <Button variant="ghost" size="sm" onClick={() => setSelectedDates([])} className="text-slate-500 hover:text-slate-900">
                      Clear
                    </Button>
                    <Button size="sm" className="bg-brand-teal text-white hover:bg-teal-700 shadow-sm px-4" onClick={handleSyncDates}>
                      Apply Changes
                    </Button>
                  </div>
                </>
              ) : (
                <div className="p-4 w-[280px]">
                  <div className="flex justify-between items-center mb-4 bg-slate-100 rounded-md p-1">
                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setPickerYear(y => y - 1)}>&lt;</Button>
                    <span className="font-bold text-slate-700">{pickerYear}</span>
                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setPickerYear(y => y + 1)}>&gt;</Button>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    {monthNames.map((m, i) => {
                      const isCurrentMonth = currentMonthDate.getMonth() === i && currentMonthDate.getFullYear() === pickerYear;
                      return (
                        <Button 
                          key={m} 
                          variant={isCurrentMonth ? "default" : "outline"}
                          className={`h-12 ${isCurrentMonth ? 'bg-brand-teal text-white hover:bg-teal-700' : 'hover:bg-slate-100 hover:text-brand-teal text-slate-700'}`}
                          onClick={() => {
                            const mStr = String(i + 1).padStart(2, '0');
                            setSelectedDate(`${pickerYear}-${mStr}-01`);
                            setPickerView('days');
                          }}
                        >
                          {m}
                        </Button>
                      );
                    })}
                  </div>
                  <div className="mt-4 flex justify-between">
                    <Button variant="ghost" size="sm" onClick={() => setPickerView('days')}>Back</Button>
                    <Button variant="ghost" size="sm" className="text-blue-600" onClick={() => {
                      const now = new Date();
                      setSelectedDate(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`);
                      setPickerYear(now.getFullYear());
                      setPickerView('days');
                    }}>This month</Button>
                  </div>
                </div>
              )}
            </PopoverContent>
          </Popover>
          </div>
          <Button onClick={handleOpenCommonLogs} size="sm" variant="outline" className="text-slate-700">
            <History className="w-4 h-4 mr-1" />
            Common Logs
          </Button>
          {user?.role?.toLowerCase() === 'admin' && (
            <Button onClick={() => { setSettingsForm(settings); setIsSettingsOpen(true); }} size="icon" variant="outline" title="Settings">
              <Settings2 className="w-4 h-4 text-slate-600" />
            </Button>
          )}
          <Button onClick={() => {
            setDownloadStartDate("");
            setDownloadEndDate("");
            setIsPdfDialogOpen(true);
          }} size="sm" variant="outline" className="text-slate-700">
            <Download className="w-4 h-4 mr-1" />
            PDF
          </Button>
          <Button 
            onClick={() => setExplanationMode(!explanationMode)} 
            size="sm" 
            variant={explanationMode ? "default" : "outline"} 
            className={explanationMode ? "bg-amber-500 hover:bg-amber-600 text-white font-bold h-9" : "text-slate-700 h-9"}
          >
            <Lightbulb className="w-4 h-4 mr-1" />
            {explanationMode ? "Explanation Mode: ON" : "Explanation Mode"}
          </Button>
          {explanationMode && entries.some(e => Array.isArray(e.highlightedByUserIds) && e.highlightedByUserIds.includes(user?.id || user?._id)) && (
            <Button onClick={handleClearAllHighlights} size="sm" variant="outline" className="text-amber-600 border-amber-200 hover:bg-amber-50 hover:text-amber-700 h-9">
              <Star className="w-4 h-4 mr-1 fill-current" />
              Clear Highlights
            </Button>
          )}
          <Button onClick={handleAddRow} size="sm" className="bg-brand-teal hover:bg-teal-700 h-9">
            <Plus className="w-4 h-4 mr-1" />
            Add Row
          </Button>
          <Button onClick={() => setIsFullScreen(!isFullScreen)} size="icon" variant="outline">
            {isFullScreen ? <Minimize className="w-4 h-4 text-slate-600" /> : <Maximize className="w-4 h-4 text-slate-600" />}
          </Button>
        </div>
      </div>

      <Dialog open={isSettingsOpen} onOpenChange={setIsSettingsOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Calendar Settings ({monthYear})</DialogTitle>
            <DialogDescription>
              Configure the working day offsets for automatic date calculations for this specific month.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="grid grid-cols-4 items-center gap-4">
              <Label className="text-right col-span-2">Script Date Offset</Label>
              <Input type="number" className="col-span-2" value={settingsForm.scriptDateOffset ?? ""} onChange={e => setSettingsForm({...settingsForm, scriptDateOffset: e.target.value ? Number(e.target.value) : undefined})} />
            </div>
            <div className="grid grid-cols-4 items-center gap-4">
              <Label className="text-right col-span-2">Shoot Date Offset</Label>
              <Input type="number" className="col-span-2" value={settingsForm.shootDateOffset ?? ""} onChange={e => setSettingsForm({...settingsForm, shootDateOffset: e.target.value ? Number(e.target.value) : undefined})} />
            </div>
            <div className="grid grid-cols-4 items-center gap-4">
              <Label className="text-right col-span-2">Editing Start Offset</Label>
              <Input type="number" className="col-span-2" value={settingsForm.editingStartOffset ?? ""} onChange={e => setSettingsForm({...settingsForm, editingStartOffset: e.target.value ? Number(e.target.value) : undefined})} />
            </div>
            <div className="grid grid-cols-4 items-center gap-4">
              <Label className="text-right col-span-2">Approval Offset</Label>
              <Input type="number" className="col-span-2" value={settingsForm.approvalOffset ?? ""} onChange={e => setSettingsForm({...settingsForm, approvalOffset: e.target.value ? Number(e.target.value) : undefined})} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsSettingsOpen(false)}>Cancel</Button>
            <Button onClick={handleSaveSettings} className="bg-brand-teal text-white">Save Changes</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={showReasonDialog} onOpenChange={setShowReasonDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reason Required</DialogTitle>
            <DialogDescription>
              Please provide a reason for changing the status to <span className="font-semibold text-brand-teal">{pendingStatusChange}</span>.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <Label className="mb-2 block">Reason</Label>
            <Input 
              value={statusChangeReason} 
              onChange={e => setStatusChangeReason(e.target.value)} 
              placeholder="Enter reason..." 
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (!statusChangeReason.trim()) {
                    toast.error("Reason is required");
                    return;
                  }
                  setShowReasonDialog(false);
                  proceedWithStatusChange(pendingStatusChange!, statusChangeReason);
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowReasonDialog(false)}>Cancel</Button>
            <Button 
              onClick={() => {
                if (!statusChangeReason.trim()) {
                  toast.error("Reason is required");
                  return;
                }
                setShowReasonDialog(false);
                proceedWithStatusChange(pendingStatusChange!, statusChangeReason);
              }}
              className="bg-brand-teal text-white"
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isPdfDialogOpen} onOpenChange={setIsPdfDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Download PDF</DialogTitle>
            <DialogDescription>
              Select the columns you want to include in the PDF export.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <div className="grid grid-cols-2 gap-4 mb-6">
              <div>
                <Label htmlFor="downloadStartDate" className="text-sm font-medium mb-1 block">Start Date (Posting)</Label>
                <Input
                  id="downloadStartDate"
                  type="date"
                  value={downloadStartDate}
                  onChange={(e) => setDownloadStartDate(e.target.value)}
                  className="w-full"
                />
              </div>
              <div>
                <Label htmlFor="downloadEndDate" className="text-sm font-medium mb-1 block">End Date (Posting)</Label>
                <Input
                  id="downloadEndDate"
                  type="date"
                  value={downloadEndDate}
                  onChange={(e) => setDownloadEndDate(e.target.value)}
                  className="w-full"
                />
              </div>
            </div>
            <div className="flex justify-between items-center mb-4">
              <Button 
                variant="ghost" 
                size="sm" 
                onClick={() => setSelectedColumnsForPdf(tableHeaders.filter(h => h !== ""))}
                className="h-8 text-xs"
              >
                Select All
              </Button>
              <Button 
                variant="ghost" 
                size="sm" 
                onClick={() => setSelectedColumnsForPdf([])}
                className="h-8 text-xs text-slate-500"
              >
                Clear All
              </Button>
            </div>
            <div className="columns-2 gap-4 p-1">
              {tableHeaders.filter(h => h !== "").map((header, idx) => (
                <div key={idx} className="flex items-center space-x-2 mb-3 break-inside-avoid">
                  <input
                    type="checkbox"
                    id={`col-${idx}`}
                    checked={selectedColumnsForPdf.includes(header)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedColumnsForPdf([...selectedColumnsForPdf, header]);
                      } else {
                        setSelectedColumnsForPdf(selectedColumnsForPdf.filter(h => h !== header));
                      }
                    }}
                    className="rounded border-slate-300 text-brand-teal focus:ring-brand-teal"
                  />
                  <Label htmlFor={`col-${idx}`} className="text-sm cursor-pointer">{header}</Label>
                </div>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsPdfDialogOpen(false)}>Cancel</Button>
            <Button onClick={handleDownloadPdf} disabled={isDownloadingPdf} className="bg-brand-teal text-white">
              {isDownloadingPdf ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Generating PDF...
                </>
              ) : (
                "Download"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={logsDialogOpen} onOpenChange={setLogsDialogOpen}>
        <DialogContent className="max-w-2xl p-0 border-none bg-[#fbfcfc] rounded-2xl overflow-hidden">
          <div className="px-6 py-6 flex items-start gap-4 relative">
            <div className="h-10 w-10 rounded-full bg-emerald-50 flex items-center justify-center text-emerald-600 shrink-0 mt-1">
              <History className="h-5 w-5" />
            </div>
            <div>
              <DialogTitle className="text-[22px] font-bold text-slate-900">{isCommonLogs ? "Project Activity Logs" : "Row Activity History"}</DialogTitle>
              <DialogDescription className="text-xs text-slate-500 mt-1 italic">
                {isCommonLogs ? "Combined logs for this content calendar" : "Content Calendar Row"}
              </DialogDescription>
            </div>
          </div>
          
          <div className="px-6 pb-6 max-h-[60vh] overflow-y-auto">
            {currentLogs.length === 0 ? (
              <p className="text-sm text-slate-500 text-center py-8">No activity logs found for this row.</p>
            ) : (
              <div className="relative pl-0 ml-10 space-y-6">
                {/* Continuous Timeline Line */}
                <div className="absolute left-[-15px] top-4 bottom-4 w-0.5 bg-slate-200" />
                
                {currentLogs.slice().reverse().map((log: any, i: number) => {
                  const cleanedDetails = log.details 
                    ? log.details.split(', ').filter((part: string) => !part.includes('created_at') && !part.includes('updated_at') && !part.includes('createdAt') && !part.includes('updatedAt'))
                    : [];
                  
                  const actionText = log.action === "Row created" ? "CREATED" : "UPDATED";
                  const actionColor = log.action === "Row created" ? "text-blue-600 bg-blue-50 border-blue-100" : "text-emerald-600 bg-emerald-50 border-emerald-100";

                  return (
                    <div key={i} className="relative">
                      {/* Timeline dot */}
                      <div className="absolute -left-[20px] top-[14px] h-[9px] w-[9px] rounded-full border-[2px] border-emerald-600 bg-white z-10" />
                      
                      {/* Card */}
                      <div className="bg-white p-3 rounded-xl border border-slate-100 shadow-sm ml-2">
                        <div className="flex justify-between items-center mb-1.5">
                          <div className="flex items-center gap-2">
                            <h4 className="font-bold text-slate-900 text-[14px] leading-none">{log.userName || "Unknown User"}</h4>
                            <div className={`inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold border ${actionColor} tracking-wider`}>
                              {actionText}
                            </div>
                            {isCommonLogs && log.rowConcept && (
                              <Badge className="ml-1 bg-slate-100 text-slate-600 hover:bg-slate-200 border border-slate-200 shadow-none font-medium px-1.5 py-0">
                                {log.rowConcept}
                              </Badge>
                            )}
                          </div>
                          <span className="text-[10px] text-slate-400 font-medium">
                            {new Date(log.timestamp).toLocaleString('en-US', { month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true })}
                          </span>
                        </div>
                        
                        <ul className="space-y-1 text-[12px] text-slate-600 list-disc pl-4 marker:text-slate-400 mt-1">
                          {cleanedDetails.length > 0 ? (
                            cleanedDetails.map((detail: string, j: number) => (
                              <li key={j}>{detail}</li>
                            ))
                          ) : (
                            <li>{log.details || "Initial entry created"}</li>
                          )}
                        </ul>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          
          <div className="px-6 py-4 flex justify-end">
            <Button onClick={() => setLogsDialogOpen(false)} className="bg-emerald-600 hover:bg-emerald-700 text-white px-8 rounded-lg font-medium shadow-sm">
              Close
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <div className={`no-scrollbar ${isFullScreen ? 'flex-1 overflow-auto' : 'overflow-x-auto'}`}>
        {isLoading ? (
          <div className="flex justify-center p-8 text-slate-400">
            <Loader2 className="w-6 h-6 animate-spin" />
          </div>
        ) : (
          <table className="w-full text-left border-collapse border border-slate-200 text-sm whitespace-nowrap min-w-[2000px]">
            <thead>
              <tr className="bg-slate-50">
                {tableHeaders.map((h, i) => (
                  <th 
                    key={i} 
                    className={`px-3 py-2 font-semibold text-slate-600 text-xs uppercase tracking-wider border border-slate-200 ${
                      i <= 2 ? "sticky z-20 bg-slate-50" : i === tableHeaders.length - 1 ? "sticky right-0 z-20 bg-slate-50" : ""
                    }`}
                    style={{
                      left: i === 0 ? 0 : i === 1 ? '140px' : i === 2 ? '260px' : 'auto',
                      minWidth: i === 0 ? '140px' : i === 1 ? '120px' : i === 2 ? '100px' : i === tableHeaders.length - 1 ? '80px' : 'auto',
                      maxWidth: i === 0 ? '140px' : i === 1 ? '120px' : i === 2 ? '100px' : i === tableHeaders.length - 1 ? '80px' : 'auto',
                      boxShadow: i === 2 ? 'inset -1px 0 0 0 #e2e8f0, 2px 0 4px -2px rgba(0,0,0,0.1)' : (i < 2 ? 'inset -1px 0 0 0 #e2e8f0' : i === tableHeaders.length - 1 ? 'inset 1px 0 0 0 #e2e8f0, -2px 0 4px -2px rgba(0,0,0,0.1)' : undefined)
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredEntries.length === 0 ? (
                <tr>
                  <td colSpan={tableHeaders.length} className="px-4 py-8 text-center text-slate-500">
                    {entries.length === 0 
                      ? "No entries for this month. Click \"Add Row\" or \"Select Dates\" to start planning."
                      : "No entries found matching the selected filter."}
                  </td>
                </tr>
              ) : (
                 filteredEntries.map((entry) => {
                  const isEditing = editingId === entry.id;
                  const currentUserId = user?.id || user?._id;
                  const isRowHighlighted = !!(currentUserId && Array.isArray(entry.highlightedByUserIds) && entry.highlightedByUserIds.includes(currentUserId));
                  
                  let isDue = false;
                  if (entry.postingDate) {
                    const postDate = new Date(entry.postingDate);
                    const today = new Date();
                    today.setHours(0, 0, 0, 0);
                    if (postDate <= today) {
                      const igLink = (entry.postingLinkOfIg || "").trim();
                      const finalLink = (entry.finalPostLink || entry.finalReelLink || "").trim();
                      if (entry.postReel === 'Story') {
                        if (!finalLink || finalLink === '-') {
                          isDue = true;
                        }
                      } else {
                        if (!igLink && !finalLink) {
                          isDue = true;
                        }
                      }
                    }
                  }
                  
                  return (
                    <tr 
                      key={entry.id} 
                      id={`task-${entry.id}`} 
                      className={`transition-all duration-1000 ${
                        explanationMode && isRowHighlighted 
                          ? "bg-amber-100/55 border-amber-300 hover:bg-amber-100/80 border-l-[6px] border-l-amber-600 font-semibold text-amber-950 shadow-sm" 
                          : isDue 
                            ? "bg-red-50 border-red-200" 
                            : "odd:bg-white even:bg-slate-50"
                      }`}
                    >
                      {fieldKeys.map((key, index) => (
                        <td 
                          key={key} 
                          className={`px-2 py-1 border border-slate-200 max-w-[200px] transition-all duration-150 ${
                            index <= 2 ? "sticky z-10" : ""
                          } ${
                            explanationMode 
                              ? "hover:bg-amber-200 hover:border-amber-500 hover:text-slate-950 hover:font-bold hover:shadow-md hover:scale-[1.015] hover:z-20 relative cursor-pointer" 
                              : ""
                          }`}
                          style={{
                            left: index === 0 ? 0 : index === 1 ? '140px' : index === 2 ? '260px' : 'auto',
                            minWidth: index === 0 ? '140px' : index === 1 ? '120px' : index === 2 ? '100px' : 'auto',
                            maxWidth: index === 0 ? '140px' : index === 1 ? '120px' : index === 2 ? '100px' : 'auto',
                            backgroundColor: index <= 2 ? 'inherit' : undefined,
                            boxShadow: index === 2 ? 'inset -1px 0 0 0 #e2e8f0, 2px 0 4px -2px rgba(0,0,0,0.1)' : (index < 2 ? 'inset -1px 0 0 0 #e2e8f0' : undefined)
                          }}
                        >
                          {isEditing ? (
                            key === "postReel" ? (
                              <Select 
                                value={editForm[key] || ""} 
                                onValueChange={(val) => {
                                  const updates: any = { postReel: val };
                                  if (val === 'Post') {
                                    updates.shootLink = '-';
                                    updates.thumbnailLink = '-';
                                    updates.shootDate = '-';
                                    updates.thumbnailDate = '-';
                                  } else if (val === 'Story') {
                                    updates.scriptDate = '-';
                                    updates.scriptLink = '-';
                                    updates.shootDate = '-';
                                    updates.shootLink = '-';
                                    updates.thumbnailDate = '-';
                                    updates.thumbnailLink = '-';
                                    updates.captionDate = '-';
                                    updates.caption = '-';
                                    updates.postingLinkOfIg = '-';
                                    updates.finalPostLink = '-';
                                    updates.assignedBrandPersonIds = [];
                                  }
                                  setEditForm({ ...editForm, ...updates });
                                }}
                              >
                                <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Type" /></SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="Post">Post</SelectItem>
                                  <SelectItem value="Reel">Reel</SelectItem>
                                  <SelectItem value="Story">Story</SelectItem>
                                </SelectContent>
                              </Select>
                            ) : key === "isApproved" ? (
                              <Select 
                                value={editForm[key] || ""} 
                                onValueChange={(val) => setEditForm({ ...editForm, [key]: val })}
                              >
                                <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Approval" /></SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="Yes">Yes</SelectItem>
                                  <SelectItem value="No">No</SelectItem>
                                  <SelectItem value="Pending">Pending</SelectItem>
                                </SelectContent>
                              </Select>
                            ) : key === "postingDate" ? (
                              <Input 
                                type="date"
                                className="h-8 text-xs px-2 py-1 min-w-[120px]"
                                value={editForm[key] || ""}
                                onChange={(e) => handleDaySelect(e.target.value)}
                              />
                            ) : ["scriptDate", "shootDate", "editingStart", "captionDate", "thumbnailDate", "actualPostingDate", "approval"].includes(key) ? (
                              ( (key === 'thumbnailDate' || key === 'shootDate') && editForm.postReel === 'Post') ||
                              ( ['scriptDate', 'shootDate', 'thumbnailDate', 'captionDate'].includes(key) && editForm.postReel === 'Story') ? (
                                <div className="text-slate-400 text-center w-full">-</div>
                              ) : (
                                <Input 
                                  type="date"
                                  className="h-8 text-xs px-2 py-1 min-w-[120px]"
                                  value={editForm[key] || ((key === 'captionDate' || key === 'thumbnailDate') ? (editForm.editingStart || "") : "")}
                                  onChange={(e) => setEditForm({ ...editForm, [key]: e.target.value })}
                                />
                              )
                            ) : key === "assignedBrandPersonIds" ? (
                              editForm.postReel === 'Story' ? (
                                <div className="text-slate-400 text-center w-full">-</div>
                              ) : (
                                <div className="w-[200px]">
                                  <MultiSelect 
                                    options={employees.map(e => ({ value: e.id, label: e.name || `${e.firstName} ${e.lastName}` }))}
                                    selected={Array.isArray(editForm[key]) ? editForm[key] : (typeof editForm[key] === 'string' ? editForm[key].split(',').filter(Boolean) : [])}
                                    onChange={(vals) => setEditForm({ ...editForm, [key]: vals })}
                                    placeholder="Select Employees"
                                  />
                                </div>
                              )
                            ) : (
                              ( (key === 'thumbnailLink' || key === 'shootLink') && editForm.postReel === 'Post') ||
                              ( ['scriptLink', 'shootLink', 'thumbnailLink', 'caption', 'postingLinkOfIg', 'finalPostLink'].includes(key) && editForm.postReel === 'Story') ? (
                                <div className="text-slate-400 text-center w-full">-</div>
                              ) : key === 'caption' ? (
                                <textarea
                                  className="w-full text-xs p-1.5 border border-slate-200 rounded-md focus:outline-none focus:ring-1 focus:ring-brand-teal min-h-[60px] resize-y"
                                  value={editForm[key] || ""}
                                  onChange={(e) => setEditForm({ ...editForm, [key]: e.target.value })}
                                  placeholder="Enter caption with spacing..."
                                  rows={3}
                                />
                              ) : (
                                <Input 
                                  className="h-8 text-xs px-2 py-1"
                                  value={editForm[key] || ""}
                                  onChange={(e) => setEditForm({ ...editForm, [key]: e.target.value })}
                                  placeholder={key === "reference" || key.toLowerCase().includes("link") ? "Enter link(s) separated by spaces or commas..." : ""}
                                />
                              )
                            )
                          ) : (
                             <div 
                               className="px-1 cursor-pointer min-h-[20px] flex items-center text-xs w-full overflow-hidden" 
                               onClick={() => startEditing(entry)}
                               title={entry[key] || ""}
                             >
                               {(() => {
                                 if (['thumbnailDate', 'thumbnailLink', 'shootDate', 'shootLink'].includes(key) && entry.postReel === 'Post') {
                                   return <span className="text-slate-400 text-center w-full">-</span>;
                                 }
                                 if (['scriptDate', 'scriptLink', 'shootDate', 'shootLink', 'thumbnailDate', 'thumbnailLink', 'captionDate', 'caption', 'postingLinkOfIg', 'finalPostLink', 'assignedBrandPersonIds'].includes(key) && entry.postReel === 'Story') {
                                   return <span className="text-slate-400 text-center w-full">-</span>;
                                 }
                                 if (entry[key] && (key.toLowerCase().includes("link") || key === "reference")) {
                                   const matches = entry[key].match(/(https?:\/\/[^\s,;]+|www\.[^\s,;]+)/gi);
                                   if (matches && matches.length > 0) {
                                     return (
                                       <div className="flex flex-wrap items-center gap-1 w-full">
                                         {matches.map((matchUrl: string, idx: number) => {
                                           let url = matchUrl;
                                           if (url.toLowerCase().startsWith("www.")) {
                                             url = "https://" + url;
                                           }
                                           return (
                                             <div key={idx} className="flex items-center gap-1 bg-blue-50 text-blue-600 px-1 py-0.5 rounded border border-blue-100 text-[10px] select-none hover:bg-blue-100/70 transition-colors">
                                               <a href={url} target="_blank" rel="noopener noreferrer" className="hover:underline font-semibold max-w-[80px] truncate" title={url} onClick={e => e.stopPropagation()}>
                                                 {matches.length > 1 ? `Link ${idx + 1}` : "Link"}
                                               </a>
                                               <Button
                                                 variant="ghost"
                                                 size="icon"
                                                 className="h-3 w-3 p-0 hover:bg-blue-200/55 rounded flex items-center justify-center shadow-none border-none bg-transparent"
                                                 onClick={(e) => {
                                                   e.stopPropagation();
                                                   navigator.clipboard.writeText(url);
                                                   toast.success("Link copied!");
                                                 }}
                                               >
                                                 <Copy className="h-2 w-2 text-blue-400 hover:text-blue-600" />
                                               </Button>
                                             </div>
                                           );
                                         })}
                                       </div>
                                     );
                                   }
                                 }

                                 if (key === "assignedBrandPersonIds") {
                                   const bpIdsRaw = entry[key];
                                   const ids = Array.isArray(bpIdsRaw) ? bpIdsRaw : (typeof bpIdsRaw === 'string' ? bpIdsRaw.split(',').filter(Boolean) : []);
                                   const names = ids.map((id: string) => {
                                     const emp = employees.find((e: any) => e.id === id);
                                     return emp ? (emp.name || `${emp.firstName} ${emp.lastName}`) : id;
                                   });
                                   return (
                                     <span className="truncate flex-1" title={names.join(', ')}>
                                       {names.length > 0 ? names.join(', ') : null}
                                     </span>
                                   );
                                 }

                                 return (
                                   <>
                                     <span className="truncate flex-1">
                                       {formatDateDisplay(entry[key] || ((key === 'captionDate' || key === 'thumbnailDate') ? entry.editingStart : null)) || null}
                                     </span>
                                     {key === "caption" && entry[key] && (
                                       <Button
                                         variant="ghost"
                                         size="icon"
                                         className="h-5 w-5 ml-1 p-0 text-slate-400 hover:text-brand-teal shrink-0 bg-transparent shadow-none hover:bg-slate-100"
                                         onClick={(e) => {
                                           e.stopPropagation();
                                           const rawText = String(entry[key] || "");
                                           const plainText = rawText.replace(/\r?\n/g, "\r\n");
                                           const htmlText = rawText.replace(/\r?\n/g, "<br>");
                                           
                                           try {
                                             const clipboardItem = new ClipboardItem({
                                               "text/plain": new Blob([plainText], { type: "text/plain" }),
                                               "text/html": new Blob([htmlText], { type: "text/html" })
                                             });
                                             navigator.clipboard.write([clipboardItem]).then(() => {
                                               toast.success("Caption copied with formatting!");
                                             }).catch(() => {
                                               navigator.clipboard.writeText(plainText);
                                               toast.success("Caption copied!");
                                             });
                                           } catch (err) {
                                             navigator.clipboard.writeText(plainText);
                                             toast.success("Caption copied!");
                                           }
                                         }}
                                         title="Copy Caption"
                                       >
                                         <Copy className="h-3 w-3" />
                                       </Button>
                                     )}
                                     {key === "postingDate" && isDue && (
                                       <span className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-100 text-red-800 shrink-0">
                                         Due
                                       </span>
                                     )}
                                   </>
                                 );
                               })()}
                             </div>
                           )}
                        </td>
                      ))}
                      <td 
                        className="px-2 py-1 border border-slate-200 min-w-[80px] sticky right-0 z-10"
                        style={{
                          backgroundColor: 'inherit',
                          boxShadow: 'inset 1px 0 0 0 #e2e8f0, -2px 0 4px -2px rgba(0,0,0,0.1)'
                        }}
                      >
                        <div className="flex items-center justify-center gap-1">
                          {isEditing ? (
                            <>
                              <Button size="icon" variant="ghost" className="h-6 w-6 text-emerald-600 hover:bg-emerald-50" onClick={handleSaveRow} disabled={isSaving}>
                                {isSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                              </Button>
                              <Button size="icon" variant="ghost" className="h-6 w-6 text-slate-400 hover:bg-slate-100" onClick={cancelEditing}>
                                <X className="w-3.5 h-3.5" />
                              </Button>
                            </>
                          ) : (
                            <>
                              {explanationMode && (
                                <Button 
                                  size="icon" 
                                  variant="ghost" 
                                  className={`h-6 w-6 ${isRowHighlighted ? "text-amber-500 hover:text-amber-600 hover:bg-amber-50" : "text-slate-400 hover:text-amber-500 hover:bg-slate-50"}`} 
                                  onClick={() => handleToggleHighlight(entry)} 
                                  title={isRowHighlighted ? "Remove Highlight" : "Highlight Row"}
                                >
                                  <Star className="h-3.5 w-3.5" style={{ fill: isRowHighlighted ? "currentColor" : "none" }} />
                                </Button>
                              )}
                              <Button size="icon" variant="ghost" className="h-6 w-6 text-slate-500 hover:text-brand-teal hover:bg-slate-50" onClick={() => handleOpenLogs(entry)} title="Activity Logs">
                                <History className="h-3.5 w-3.5" />
                              </Button>
                              <Button size="icon" variant="ghost" className="h-6 w-6 text-red-500 hover:text-red-700 hover:bg-red-50" onClick={() => handleDeleteRow(entry.id)}>
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

