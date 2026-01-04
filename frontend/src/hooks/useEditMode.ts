import { useState, useCallback, useRef, useEffect } from 'react';
import type {
  Channel,
  ChannelSnapshot,
  EditModeState,
  StagedOperation,
  UndoEntry,
  EditModeSummary,
  CommitResult,
  UseEditModeReturn,
  ApiCallSpec,
  Logo,
} from '../types';
import * as api from '../services/api';

// Helper to create a snapshot from a channel
function createSnapshot(channel: Channel): ChannelSnapshot {
  return {
    id: channel.id,
    channel_number: channel.channel_number,
    name: channel.name,
    channel_group_id: channel.channel_group_id,
    streams: [...channel.streams],
  };
}

// Generate a unique ID
function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

// Compute which channels are modified by comparing working copy to baseline
function computeModifiedChannelIds(
  workingCopy: Channel[],
  baselineSnapshot: ChannelSnapshot[]
): Set<number> {
  const modified = new Set<number>();

  // Check each channel in working copy against baseline
  for (const channel of workingCopy) {
    // New channels (negative IDs) are always modified
    if (channel.id < 0) {
      modified.add(channel.id);
      continue;
    }

    const baseline = baselineSnapshot.find((s) => s.id === channel.id);
    if (!baseline) {
      // Channel exists in working copy but not baseline - it's new
      modified.add(channel.id);
      continue;
    }

    // Compare relevant fields
    if (
      channel.channel_number !== baseline.channel_number ||
      channel.name !== baseline.name ||
      channel.channel_group_id !== baseline.channel_group_id ||
      JSON.stringify(channel.streams) !== JSON.stringify(baseline.streams)
    ) {
      modified.add(channel.id);
    }
  }

  return modified;
}

// Initial state for edit mode
function createInitialState(): EditModeState {
  return {
    isActive: false,
    enteredAt: null,
    baselineSnapshot: [],
    workingCopy: [],
    stagedOperations: [],
    localUndoStack: [],
    localRedoStack: [],
    modifiedChannelIds: new Set(),
    nextTempId: -1,
    tempIdMap: new Map(),
    currentBatch: null,
    stagedGroups: new Map(),
    newGroupNameToTempId: new Map(),
    nextTempGroupId: -1000, // Start at -1000 to distinguish from channel temp IDs
  };
}

export interface UseEditModeOptions {
  channels: Channel[];
  onChannelsChange: (channels: Channel[]) => void;
  onCommitComplete?: (createdGroupIds: number[]) => void;
  onError?: (message: string) => void;
}

export function useEditMode({
  channels,
  onChannelsChange,
  onCommitComplete,
  onError,
}: UseEditModeOptions): UseEditModeReturn {
  const [state, setState] = useState<EditModeState>(createInitialState);
  const [isCommitting, setIsCommitting] = useState(false);

  // Track real channels for conflict detection
  const realChannelsRef = useRef<Channel[]>(channels);

  // Use a ref for next temp ID to avoid React batching issues when creating multiple channels in a loop
  const nextTempIdRef = useRef(-1);
  realChannelsRef.current = channels;

  // Use a ref for next temp group ID to avoid React batching issues
  const nextTempGroupIdRef = useRef(-1000);

  // Enter edit mode - snapshot current state
  const enterEditMode = useCallback(() => {
    const snapshot = channels.map(createSnapshot);
    const workingCopy = channels.map((ch) => ({ ...ch, streams: [...ch.streams] }));

    // Reset temp ID refs
    nextTempIdRef.current = -1;
    nextTempGroupIdRef.current = -1000;

    setState({
      isActive: true,
      enteredAt: Date.now(),
      baselineSnapshot: snapshot,
      workingCopy,
      stagedOperations: [],
      localUndoStack: [],
      localRedoStack: [],
      modifiedChannelIds: new Set(),
      nextTempId: -1,
      tempIdMap: new Map(),
      currentBatch: null,
      stagedGroups: new Map(),
      newGroupNameToTempId: new Map(),
      nextTempGroupId: -1000,
    });
  }, [channels]);

  // Exit edit mode (discards changes)
  const exitEditMode = useCallback(() => {
    nextTempIdRef.current = -1; // Reset temp ID ref
    setState(createInitialState());
  }, []);

  // Discard all staged changes
  const discard = useCallback(() => {
    nextTempIdRef.current = -1; // Reset temp ID ref
    setState(createInitialState());
  }, []);

  // Apply a staged operation to the working copy
  const applyOperationToWorkingCopy = useCallback(
    (
      workingCopy: Channel[],
      operation: StagedOperation
    ): Channel[] => {
      const { apiCall } = operation;

      switch (apiCall.type) {
        case 'updateChannel': {
          return workingCopy.map((ch) =>
            ch.id === apiCall.channelId
              ? { ...ch, ...apiCall.data }
              : ch
          );
        }

        case 'addStreamToChannel': {
          return workingCopy.map((ch) =>
            ch.id === apiCall.channelId && !ch.streams.includes(apiCall.streamId)
              ? { ...ch, streams: [...ch.streams, apiCall.streamId] }
              : ch
          );
        }

        case 'removeStreamFromChannel': {
          return workingCopy.map((ch) =>
            ch.id === apiCall.channelId
              ? { ...ch, streams: ch.streams.filter((id) => id !== apiCall.streamId) }
              : ch
          );
        }

        case 'reorderChannelStreams': {
          return workingCopy.map((ch) =>
            ch.id === apiCall.channelId
              ? { ...ch, streams: apiCall.streamIds }
              : ch
          );
        }

        case 'bulkAssignChannelNumbers': {
          const channelIdToNumber = new Map<number, number>();
          apiCall.channelIds.forEach((id, index) => {
            channelIdToNumber.set(id, (apiCall.startingNumber ?? 1) + index);
          });
          return workingCopy.map((ch) =>
            channelIdToNumber.has(ch.id)
              ? { ...ch, channel_number: channelIdToNumber.get(ch.id)! }
              : ch
          );
        }

        case 'createChannel': {
          // New channels handled separately
          return workingCopy;
        }

        case 'deleteChannel': {
          return workingCopy.filter((ch) => ch.id !== apiCall.channelId);
        }

        case 'createGroup': {
          // Group creation doesn't affect the working copy of channels
          // It's a separate entity handled at commit time
          return workingCopy;
        }

        case 'deleteChannelGroup': {
          // Group deletion doesn't affect the working copy of channels
          // It's a separate entity handled at commit time
          return workingCopy;
        }

        default:
          return workingCopy;
      }
    },
    []
  );

  // Stage an operation
  const stageOperation = useCallback(
    (apiCall: ApiCallSpec, description: string, affectedChannelIds: number[]) => {
      setState((prev) => {
        if (!prev.isActive) {
          return prev;
        }

        // Get before snapshot from current working copy
        const beforeSnapshot = prev.workingCopy
          .filter((ch) => affectedChannelIds.includes(ch.id))
          .map(createSnapshot);

        // Create the operation
        const operation: StagedOperation = {
          id: generateId(),
          timestamp: Date.now(),
          description,
          apiCall,
          beforeSnapshot,
          afterSnapshot: [], // Will be computed after applying
        };

        // Apply to working copy
        let newWorkingCopy = applyOperationToWorkingCopy(prev.workingCopy, operation);

        // Track new staged groups and their temp IDs
        let newStagedGroups = prev.stagedGroups;
        let newGroupNameToTempId = prev.newGroupNameToTempId;
        let newNextTempGroupId = prev.nextTempGroupId;

        // Handle create channel specially
        if (apiCall.type === 'createChannel') {
          const tempId = prev.nextTempId;

          // Determine channel_group_id: use existing groupId, or create/reuse a temp group for newGroupName
          let channelGroupId: number | null = apiCall.groupId ?? null;

          if (apiCall.newGroupName) {
            // Check if we already have a temp ID for this group name
            if (prev.newGroupNameToTempId.has(apiCall.newGroupName)) {
              channelGroupId = prev.newGroupNameToTempId.get(apiCall.newGroupName)!;
            } else {
              // Create a new staged group
              const tempGroupId = nextTempGroupIdRef.current;
              nextTempGroupIdRef.current -= 1;

              const newGroup = {
                id: tempGroupId,
                name: apiCall.newGroupName,
                channel_count: 0, // Will be updated as channels are added
              };

              // Update maps (need to create new Map instances for immutability)
              newStagedGroups = new Map(prev.stagedGroups);
              newStagedGroups.set(tempGroupId, newGroup);

              newGroupNameToTempId = new Map(prev.newGroupNameToTempId);
              newGroupNameToTempId.set(apiCall.newGroupName, tempGroupId);

              newNextTempGroupId = tempGroupId - 1;
              channelGroupId = tempGroupId;
            }
          }

          const newChannel: Channel = {
            id: tempId,
            channel_number: apiCall.channelNumber ?? null,
            name: apiCall.name,
            channel_group_id: channelGroupId,
            tvg_id: apiCall.tvgId ?? null,
            tvc_guide_stationid: null,
            epg_data_id: null,
            streams: [],
            stream_profile_id: null,
            uuid: `temp-${tempId}`,
            logo_id: apiCall.logoId ?? null,
            auto_created: false,
            auto_created_by: null,
            auto_created_by_name: null,
            _stagedLogoUrl: apiCall.logoUrl,
          };
          newWorkingCopy = [...newWorkingCopy, newChannel];
        }

        // Compute after snapshot
        const afterSnapshot = newWorkingCopy
          .filter((ch) => affectedChannelIds.includes(ch.id) || ch.id === prev.nextTempId)
          .map(createSnapshot);

        operation.afterSnapshot = afterSnapshot;

        // Update modified channel IDs
        const newModifiedIds = new Set(prev.modifiedChannelIds);
        affectedChannelIds.forEach((id) => newModifiedIds.add(id));
        if (apiCall.type === 'createChannel') {
          newModifiedIds.add(prev.nextTempId);
        }

        // Handle batching vs immediate undo entry
        let newUndoStack = prev.localUndoStack;
        let newCurrentBatch = prev.currentBatch;

        if (prev.currentBatch !== null) {
          // We're in a batch - add operation to the current batch, don't create undo entry yet
          newCurrentBatch = {
            ...prev.currentBatch,
            operations: [...prev.currentBatch.operations, operation],
          };
        } else {
          // Not in a batch - create single-operation undo entry immediately
          const undoEntry: UndoEntry = {
            id: generateId(),
            timestamp: Date.now(),
            description,
            operations: [operation],
          };
          newUndoStack = [...prev.localUndoStack, undoEntry];
        }

        const newState = {
          ...prev,
          workingCopy: newWorkingCopy,
          stagedOperations: [...prev.stagedOperations, operation],
          localUndoStack: newUndoStack,
          localRedoStack: [], // Clear redo on new operation
          modifiedChannelIds: newModifiedIds,
          nextTempId: apiCall.type === 'createChannel' ? prev.nextTempId - 1 : prev.nextTempId,
          currentBatch: newCurrentBatch,
          stagedGroups: newStagedGroups,
          newGroupNameToTempId: newGroupNameToTempId,
          nextTempGroupId: newNextTempGroupId,
        };
        return newState;
      });
    },
    [applyOperationToWorkingCopy]
  );

  // Staging functions for each operation type
  const stageUpdateChannel = useCallback(
    (channelId: number, data: Partial<Channel>, description: string) => {
      stageOperation({ type: 'updateChannel', channelId, data }, description, [channelId]);
    },
    [stageOperation]
  );

  const stageAddStream = useCallback(
    (channelId: number, streamId: number, description: string) => {
      stageOperation(
        { type: 'addStreamToChannel', channelId, streamId },
        description,
        [channelId]
      );
    },
    [stageOperation]
  );

  const stageRemoveStream = useCallback(
    (channelId: number, streamId: number, description: string) => {
      stageOperation(
        { type: 'removeStreamFromChannel', channelId, streamId },
        description,
        [channelId]
      );
    },
    [stageOperation]
  );

  const stageReorderStreams = useCallback(
    (channelId: number, streamIds: number[], description: string) => {
      stageOperation(
        { type: 'reorderChannelStreams', channelId, streamIds },
        description,
        [channelId]
      );
    },
    [stageOperation]
  );

  const stageBulkAssignNumbers = useCallback(
    (channelIds: number[], startingNumber: number, description: string) => {
      stageOperation(
        { type: 'bulkAssignChannelNumbers', channelIds, startingNumber },
        description,
        channelIds
      );
    },
    [stageOperation]
  );

  const stageCreateChannel = useCallback(
    (name: string, channelNumber?: number, groupId?: number, newGroupName?: string, logoId?: number, logoUrl?: string, tvgId?: string): number => {
      // Use ref to get unique temp ID even when called in a loop (React batching issue)
      const tempId = nextTempIdRef.current;
      nextTempIdRef.current -= 1; // Decrement immediately for next call
      stageOperation(
        { type: 'createChannel', name, channelNumber, groupId, newGroupName, logoId, logoUrl, tvgId },
        `Create channel "${name}"`,
        []
      );
      return tempId;
    },
    [stageOperation]
  );

  const stageDeleteChannel = useCallback(
    (channelId: number, description: string) => {
      stageOperation(
        { type: 'deleteChannel', channelId },
        description,
        [channelId]
      );
    },
    [stageOperation]
  );

  const stageCreateGroup = useCallback(
    (name: string) => {
      stageOperation(
        { type: 'createGroup', name },
        `Create group "${name}"`,
        [] // No channels directly affected
      );
    },
    [stageOperation]
  );

  const stageDeleteChannelGroup = useCallback(
    (groupId: number, description: string) => {
      stageOperation(
        { type: 'deleteChannelGroup', groupId },
        description,
        [] // No channels directly affected
      );
    },
    [stageOperation]
  );

  // Add a newly created channel to the working copy
  // This is used when a channel is created via API during edit mode
  const addChannelToWorkingCopy = useCallback(
    (channel: Channel) => {
      setState((prev) => {
        if (!prev.isActive) return prev;
        // Check if channel already exists
        if (prev.workingCopy.some((ch) => ch.id === channel.id)) {
          return prev;
        }
        return {
          ...prev,
          workingCopy: [...prev.workingCopy, { ...channel, streams: [...channel.streams] }],
        };
      });
    },
    []
  );

  // Start a batch of operations that will be grouped as a single undo entry
  const startBatch = useCallback((description: string) => {
    setState((prev) => {
      if (!prev.isActive) return prev;
      // If already in a batch, don't start a new one
      if (prev.currentBatch !== null) {
        console.warn('startBatch called while already in a batch');
        return prev;
      }
      return {
        ...prev,
        currentBatch: {
          description,
          operations: [],
        },
      };
    });
  }, []);

  // End the current batch and create a single undo entry for all collected operations
  const endBatch = useCallback(() => {
    setState((prev) => {
      if (!prev.isActive || prev.currentBatch === null) return prev;

      // If no operations were staged during the batch, just clear it
      if (prev.currentBatch.operations.length === 0) {
        return {
          ...prev,
          currentBatch: null,
        };
      }

      // Create a single undo entry for all operations in the batch
      const undoEntry: UndoEntry = {
        id: generateId(),
        timestamp: Date.now(),
        description: prev.currentBatch.description,
        operations: prev.currentBatch.operations,
      };

      return {
        ...prev,
        localUndoStack: [...prev.localUndoStack, undoEntry],
        localRedoStack: [], // Clear redo on new batch
        currentBatch: null,
      };
    });
  }, []);

  // Local undo within edit session
  const localUndo = useCallback(() => {
    setState((prev) => {
      if (!prev.isActive || prev.localUndoStack.length === 0) return prev;

      const lastEntry = prev.localUndoStack[prev.localUndoStack.length - 1];

      // Undo all operations in the entry, in reverse order
      let newWorkingCopy = [...prev.workingCopy];
      let newStagedOperations = [...prev.stagedOperations];

      // Process operations in reverse order
      for (let i = lastEntry.operations.length - 1; i >= 0; i--) {
        const operation = lastEntry.operations[i];

        // Restore working copy from before snapshot
        for (const snapshot of operation.beforeSnapshot) {
          const index = newWorkingCopy.findIndex((ch) => ch.id === snapshot.id);
          if (index >= 0) {
            newWorkingCopy[index] = {
              ...newWorkingCopy[index],
              channel_number: snapshot.channel_number,
              name: snapshot.name,
              channel_group_id: snapshot.channel_group_id,
              streams: [...snapshot.streams],
            };
          } else if (operation.apiCall.type === 'deleteChannel') {
            // If undoing a delete, restore the channel from baseline
            const baselineChannel = prev.baselineSnapshot.find((s) => s.id === snapshot.id);
            if (baselineChannel) {
              // Find the original channel from baseline to get all fields
              const originalChannel = realChannelsRef.current.find((ch) => ch.id === snapshot.id);
              if (originalChannel) {
                newWorkingCopy = [
                  ...newWorkingCopy,
                  {
                    ...originalChannel,
                    channel_number: baselineChannel.channel_number,
                    name: baselineChannel.name,
                    channel_group_id: baselineChannel.channel_group_id,
                    streams: [...baselineChannel.streams],
                  },
                ];
              }
            }
          }
        }

        // If it was a create channel, remove the temp channel
        if (operation.apiCall.type === 'createChannel') {
          const tempId = operation.afterSnapshot[0]?.id;
          if (tempId && tempId < 0) {
            newWorkingCopy = newWorkingCopy.filter((ch) => ch.id !== tempId);
          }
        }

        // Remove from staged operations
        const operationIndex = newStagedOperations.findIndex(
          (op) => op.id === operation.id
        );
        if (operationIndex >= 0) {
          newStagedOperations = newStagedOperations.filter((_, idx) => idx !== operationIndex);
        }
      }

      // Recompute which channels are actually modified compared to baseline
      const newModifiedIds = computeModifiedChannelIds(newWorkingCopy, prev.baselineSnapshot);

      return {
        ...prev,
        workingCopy: newWorkingCopy,
        stagedOperations: newStagedOperations,
        localUndoStack: prev.localUndoStack.slice(0, -1),
        localRedoStack: [...prev.localRedoStack, lastEntry],
        modifiedChannelIds: newModifiedIds,
      };
    });
  }, []);

  // Local redo within edit session
  const localRedo = useCallback(() => {
    setState((prev) => {
      if (!prev.isActive || prev.localRedoStack.length === 0) return prev;

      const entry = prev.localRedoStack[prev.localRedoStack.length - 1];

      // Redo all operations in the entry, in order
      let newWorkingCopy = [...prev.workingCopy];
      let newStagedOperations = [...prev.stagedOperations];

      for (const operation of entry.operations) {
        // Apply operation to working copy
        newWorkingCopy = applyOperationToWorkingCopy(newWorkingCopy, operation);

        // Handle create channel
        if (operation.apiCall.type === 'createChannel') {
          const snapshot = operation.afterSnapshot[0];
          if (snapshot && snapshot.id < 0) {
            const newChannel: Channel = {
              id: snapshot.id,
              channel_number: snapshot.channel_number,
              name: snapshot.name,
              channel_group_id: snapshot.channel_group_id,
              tvg_id: null,
              tvc_guide_stationid: null,
              epg_data_id: null,
              streams: [...snapshot.streams],
              stream_profile_id: null,
              uuid: `temp-${snapshot.id}`,
              logo_id: null,
              auto_created: false,
              auto_created_by: null,
              auto_created_by_name: null,
            };
            newWorkingCopy = [...newWorkingCopy, newChannel];
          }
        }

        // Add back to staged operations
        newStagedOperations = [...newStagedOperations, operation];
      }

      // Recompute which channels are actually modified compared to baseline
      const newModifiedIds = computeModifiedChannelIds(newWorkingCopy, prev.baselineSnapshot);

      return {
        ...prev,
        workingCopy: newWorkingCopy,
        stagedOperations: newStagedOperations,
        localUndoStack: [...prev.localUndoStack, entry],
        localRedoStack: prev.localRedoStack.slice(0, -1),
        modifiedChannelIds: newModifiedIds,
      };
    });
  }, [applyOperationToWorkingCopy]);

  // Get summary of changes
  const getSummary = useCallback((): EditModeSummary => {
    const summary: EditModeSummary = {
      totalOperations: state.stagedOperations.length,
      channelsModified: state.modifiedChannelIds.size,
      streamsAdded: 0,
      streamsRemoved: 0,
      streamsReordered: 0,
      channelNumberChanges: 0,
      channelNameChanges: 0,
      newChannels: 0,
      deletedChannels: 0,
      newGroups: 0,
      deletedGroups: 0,
      operationDetails: [],
    };

    // Track new group names from createChannel operations (deduplicated)
    const newGroupNamesFromChannels = new Set<string>();

    for (const op of state.stagedOperations) {
      // Add to operation details
      summary.operationDetails.push({
        id: op.id,
        type: op.apiCall.type,
        description: op.description,
      });

      switch (op.apiCall.type) {
        case 'addStreamToChannel':
          summary.streamsAdded++;
          break;
        case 'removeStreamFromChannel':
          summary.streamsRemoved++;
          break;
        case 'reorderChannelStreams':
          summary.streamsReordered++;
          break;
        case 'updateChannel':
          if (op.apiCall.data.channel_number !== undefined) {
            summary.channelNumberChanges++;
          }
          if (op.apiCall.data.name !== undefined) {
            summary.channelNameChanges++;
          }
          break;
        case 'bulkAssignChannelNumbers':
          summary.channelNumberChanges += op.apiCall.channelIds.length;
          break;
        case 'createChannel':
          summary.newChannels++;
          // Track new groups from createChannel operations
          if (op.apiCall.newGroupName) {
            newGroupNamesFromChannels.add(op.apiCall.newGroupName);
          }
          break;
        case 'deleteChannel':
          summary.deletedChannels++;
          break;
        case 'createGroup':
          summary.newGroups++;
          break;
        case 'deleteChannelGroup':
          summary.deletedGroups++;
          break;
      }
    }

    // Add unique new groups from createChannel operations to the count
    summary.newGroups += newGroupNamesFromChannels.size;

    // Add operation details for each new group from channels
    for (const groupName of newGroupNamesFromChannels) {
      summary.operationDetails.unshift({
        id: `new-group-${groupName}`,
        type: 'createGroup',
        description: `Create group "${groupName}"`,
      });
    }

    return summary;
  }, [state.stagedOperations, state.modifiedChannelIds]);

  // Check for conflicts with server
  const checkForConflicts = useCallback(async (): Promise<boolean> => {
    // Compare baseline snapshot with current server state
    // For now, we just check if any modified channels have changed on server
    try {
      // Fetch fresh channels
      const allChannels: Channel[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        const response = await api.getChannels({ page, pageSize: 500 });
        allChannels.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      // Check if any modified channels differ from baseline
      for (const modifiedId of state.modifiedChannelIds) {
        if (modifiedId < 0) continue; // Skip temp channels

        const baselineSnapshot = state.baselineSnapshot.find((s) => s.id === modifiedId);
        const currentChannel = allChannels.find((ch) => ch.id === modifiedId);

        if (!currentChannel && baselineSnapshot) {
          // Channel was deleted on server
          return true;
        }

        if (currentChannel && baselineSnapshot) {
          // Check if server state differs from baseline
          if (
            currentChannel.channel_number !== baselineSnapshot.channel_number ||
            currentChannel.name !== baselineSnapshot.name ||
            JSON.stringify(currentChannel.streams) !== JSON.stringify(baselineSnapshot.streams)
          ) {
            return true;
          }
        }
      }

      return false;
    } catch {
      console.error('Failed to check for conflicts');
      return false; // Assume no conflict on error
    }
  }, [state.baselineSnapshot, state.modifiedChannelIds]);

  // Commit all staged operations to server
  const commit = useCallback(async (): Promise<CommitResult> => {
    if (!state.isActive || state.stagedOperations.length === 0) {
      return {
        success: true,
        operationsApplied: 0,
        operationsFailed: 0,
        errors: [],
        updatedChannels: channels,
      };
    }

    setIsCommitting(true);

    const result: CommitResult = {
      success: true,
      operationsApplied: 0,
      operationsFailed: 0,
      errors: [],
      updatedChannels: [],
    };

    // Copy the temp ID map for tracking new channel IDs
    const tempIdMap = new Map<number, number>();
    // Map for new group names to their created IDs
    const newGroupIdMap = new Map<string, number>();
    // Cache for logos to avoid creating duplicates
    const logoCache = new Map<string, Logo>();

    try {
      // First pass: collect all new group names that need to be created
      // (from createChannel operations with newGroupName)
      const newGroupNames = new Set<string>();
      for (const operation of state.stagedOperations) {
        if (operation.apiCall.type === 'createChannel') {
          if (operation.apiCall.newGroupName) {
            newGroupNames.add(operation.apiCall.newGroupName);
          }
        }
      }

      // Create all new groups first
      for (const groupName of newGroupNames) {
        try {
          const newGroup = await api.createChannelGroup(groupName);
          newGroupIdMap.set(groupName, newGroup.id);
          result.operationsApplied++;
        } catch (err) {
          console.error('Failed to create group:', groupName, err);
          result.success = false;
          result.operationsFailed++;
          result.errors.push({
            operationId: `create-group-${groupName}`,
            error: err instanceof Error ? err.message : 'Unknown error',
          });
          // Stop on first failure
          break;
        }
      }

      // If group creation failed, don't proceed with other operations
      if (!result.success) {
        setIsCommitting(false);
        return result;
      }

      // Execute operations sequentially
      for (const operation of state.stagedOperations) {
        try {
          const { apiCall } = operation;

          // Replace temp IDs with real IDs
          const resolveId = (id: number): number => tempIdMap.get(id) ?? id;

          switch (apiCall.type) {
            case 'updateChannel':
              await api.updateChannel(resolveId(apiCall.channelId), apiCall.data);
              break;

            case 'addStreamToChannel':
              await api.addStreamToChannel(resolveId(apiCall.channelId), apiCall.streamId);
              break;

            case 'removeStreamFromChannel':
              await api.removeStreamFromChannel(resolveId(apiCall.channelId), apiCall.streamId);
              break;

            case 'reorderChannelStreams':
              await api.reorderChannelStreams(resolveId(apiCall.channelId), apiCall.streamIds);
              break;

            case 'bulkAssignChannelNumbers':
              await api.bulkAssignChannelNumbers(
                apiCall.channelIds.map(resolveId),
                apiCall.startingNumber
              );
              break;

            case 'createChannel': {
              // Resolve group ID: use newGroupName mapping if present, otherwise use groupId
              let groupId = apiCall.groupId;
              if (apiCall.newGroupName && newGroupIdMap.has(apiCall.newGroupName)) {
                groupId = newGroupIdMap.get(apiCall.newGroupName);
              }

              // Resolve logo ID: if logoUrl is provided, create or find the logo
              let logoId = apiCall.logoId;
              if (!logoId && apiCall.logoUrl) {
                try {
                  const logo = await api.getOrCreateLogo(apiCall.name, apiCall.logoUrl, logoCache);
                  logoId = logo.id;
                } catch (logoErr) {
                  console.warn('Failed to create/find logo for channel:', apiCall.name, logoErr);
                  // Continue without logo - don't fail the whole operation
                }
              }

              const newChannel = await api.createChannel({
                name: apiCall.name,
                channel_number: apiCall.channelNumber,
                channel_group_id: groupId,
                logo_id: logoId,
                tvg_id: apiCall.tvgId,
              });
              // Track the mapping from temp ID to real ID
              const tempId = operation.afterSnapshot[0]?.id;
              if (tempId && tempId < 0) {
                tempIdMap.set(tempId, newChannel.id);
              }
              break;
            }

            case 'deleteChannel':
              await api.deleteChannel(resolveId(apiCall.channelId));
              break;

            case 'createGroup':
              await api.createChannelGroup(apiCall.name);
              break;

            case 'deleteChannelGroup':
              await api.deleteChannelGroup(apiCall.groupId);
              break;
          }

          result.operationsApplied++;
        } catch (err) {
          console.error('Failed to apply operation:', operation, err);
          result.success = false;
          result.operationsFailed++;
          result.errors.push({
            operationId: operation.id,
            error: err instanceof Error ? err.message : 'Unknown error',
          });

          // Stop on first failure
          break;
        }
      }

      // Fetch updated channels
      const allChannels: Channel[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        const response = await api.getChannels({ page, pageSize: 500 });
        allChannels.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      result.updatedChannels = allChannels;

      if (result.success) {
        // Update real channels
        onChannelsChange(allChannels);
        // Exit edit mode on success
        setState(createInitialState());
        // Pass the IDs of newly created groups to the callback
        const createdGroupIds = Array.from(newGroupIdMap.values());
        onCommitComplete?.(createdGroupIds);
      } else {
        onError?.(
          `Failed to apply ${result.operationsFailed} operation(s): ${result.errors[0]?.error}`
        );
      }
    } catch (err) {
      console.error('Commit failed:', err);
      result.success = false;
      onError?.('Commit failed: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsCommitting(false);
    }

    return result;
  }, [
    state.isActive,
    state.stagedOperations,
    channels,
    onChannelsChange,
    onCommitComplete,
    onError,
  ]);

  // Compute edit mode duration with live updates (in seconds)
  const [editModeDuration, setEditModeDuration] = useState<number | null>(null);

  useEffect(() => {
    if (!state.isActive || !state.enteredAt) {
      setEditModeDuration(null);
      return;
    }

    // Update immediately (convert ms to seconds)
    setEditModeDuration(Math.floor((Date.now() - state.enteredAt) / 1000));

    // Update every second
    const interval = setInterval(() => {
      setEditModeDuration(Math.floor((Date.now() - state.enteredAt!) / 1000));
    }, 1000);

    return () => clearInterval(interval);
  }, [state.isActive, state.enteredAt]);

  // Determine which channels to display
  const displayChannels = state.isActive ? state.workingCopy : channels;

  // Convert stagedGroups Map to array for consumers
  const stagedGroupsArray = state.isActive ? Array.from(state.stagedGroups.values()) : [];

  return {
    // State
    isEditMode: state.isActive,
    isCommitting,
    stagedOperationCount: state.localUndoStack.length,
    modifiedChannelIds: state.modifiedChannelIds,
    displayChannels,
    stagedGroups: stagedGroupsArray,
    canLocalUndo: state.localUndoStack.length > 0,
    canLocalRedo: state.localRedoStack.length > 0,
    editModeDuration,

    // Actions
    enterEditMode,
    exitEditMode,

    // Staging operations
    stageUpdateChannel,
    stageAddStream,
    stageRemoveStream,
    stageReorderStreams,
    stageBulkAssignNumbers,
    stageCreateChannel,
    stageDeleteChannel,
    stageCreateGroup,
    stageDeleteChannelGroup,
    addChannelToWorkingCopy,

    // Local undo/redo
    localUndo,
    localRedo,

    // Batch operations
    startBatch,
    endBatch,

    // Commit/Discard
    getSummary,
    commit,
    discard,
    checkForConflicts,
  };
}
