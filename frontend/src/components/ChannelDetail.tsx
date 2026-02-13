import { useState, useEffect } from 'react';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { Channel, Stream } from '../types';
import * as api from '../services/api';
import './ChannelDetail.css';

interface ChannelDetailProps {
  channel: Channel;
  onChannelUpdate: (channel: Channel) => void;
  onClose: () => void;
}

interface SortableStreamItemProps {
  stream: Stream;
  onRemove: (streamId: number) => void;
}

function SortableStreamItem({ stream, onRemove }: SortableStreamItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: stream.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style} className="stream-detail-item">
      <span className="drag-handle" {...attributes} {...listeners}>
        ⋮⋮
      </span>
      {stream.logo_url && (
        <img
          src={stream.logo_url}
          alt=""
          className="stream-logo"
          onError={(e) => {
            (e.target as HTMLImageElement).style.display = 'none';
          }}
        />
      )}
      <span className="stream-name">{stream.name}</span>
      <button
        className="remove-btn"
        onClick={() => onRemove(stream.id)}
        title="Remove stream from channel"
      >
        ✕
      </button>
    </div>
  );
}

export function ChannelDetail({
  channel,
  onChannelUpdate,
  onClose,
}: ChannelDetailProps) {
  const [streams, setStreams] = useState<Stream[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingNumber, setEditingNumber] = useState(false);
  const [channelNumber, setChannelNumber] = useState(
    channel.channel_number?.toString() ?? ''
  );
  const [saving, setSaving] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  useEffect(() => {
    loadStreams();
  }, [channel.id, channel.streams, loadStreams]);

  const loadStreams = useCallback(async () => {
    if (channel.streams.length === 0) {
      setStreams([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    try {
      const streamDetails = await api.getChannelStreams(channel.id);
      // Sort streams to match the order in channel.streams
      const orderedStreams = channel.streams
        .map((id) => streamDetails.find((s: Stream) => s.id === id))
        .filter((s): s is Stream => s !== undefined);
      setStreams(orderedStreams);
    } catch (err) {
      console.error('Failed to load streams:', err);
    } finally {
      setLoading(false);
    }
  }, [channel.id, channel.streams]);

  const handleRemoveStream = async (streamId: number) => {
    try {
      const updatedChannel = await api.removeStreamFromChannel(channel.id, streamId);
      onChannelUpdate(updatedChannel);
      setStreams((prev) => prev.filter((s) => s.id !== streamId));
    } catch (err) {
      console.error('Failed to remove stream:', err);
    }
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;

    if (over && active.id !== over.id) {
      const oldIndex = streams.findIndex((s) => s.id === active.id);
      const newIndex = streams.findIndex((s) => s.id === over.id);

      const newStreams = arrayMove(streams, oldIndex, newIndex);
      setStreams(newStreams);

      // Update on server
      try {
        const updatedChannel = await api.reorderChannelStreams(
          channel.id,
          newStreams.map((s) => s.id)
        );
        onChannelUpdate(updatedChannel);
      } catch (err) {
        console.error('Failed to reorder streams:', err);
        // Revert on error
        setStreams(streams);
      }
    }
  };

  const handleNumberSave = async () => {
    const newNumber = channelNumber.trim() === '' ? null : parseFloat(channelNumber);

    if (newNumber !== null && isNaN(newNumber)) {
      alert('Channel number must be a valid number');
      return;
    }

    setSaving(true);
    try {
      const updatedChannel = await api.updateChannel(channel.id, {
        channel_number: newNumber,
      });
      onChannelUpdate(updatedChannel);
      setEditingNumber(false);
    } catch (err) {
      console.error('Failed to update channel number:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleNumberKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleNumberSave();
    } else if (e.key === 'Escape') {
      setChannelNumber(channel.channel_number?.toString() ?? '');
      setEditingNumber(false);
    }
  };

  return (
    <div className="channel-detail">
      <div className="detail-header">
        <button className="close-btn" onClick={onClose}>
          ←
        </button>
        <div className="channel-info">
          <div className="channel-number-row">
            {editingNumber ? (
              <input
                type="text"
                value={channelNumber}
                onChange={(e) => setChannelNumber(e.target.value)}
                onBlur={handleNumberSave}
                onKeyDown={handleNumberKeyDown}
                className="number-input"
                autoFocus
                disabled={saving}
              />
            ) : (
              <span
                className="channel-number editable"
                onClick={() => setEditingNumber(true)}
                title="Click to edit channel number"
              >
                #{channel.channel_number ?? '-'}
              </span>
            )}
          </div>
          <h3 className="channel-name">{channel.name}</h3>
        </div>
      </div>

      <div className="detail-content">
        <h4>
          Assigned Streams ({streams.length})
          <span className="hint">Drag to reorder priority</span>
        </h4>

        {loading ? (
          <div className="loading">Loading streams...</div>
        ) : streams.length === 0 ? (
          <div className="empty-state">
            No streams assigned. Drag streams from the right panel to add them.
          </div>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={streams.map((s) => s.id)}
              strategy={verticalListSortingStrategy}
            >
              <div className="streams-list">
                {streams.map((stream, index) => (
                  <div key={stream.id} className="stream-row">
                    <span className="priority-number">{index + 1}</span>
                    <SortableStreamItem
                      stream={stream}
                      onRemove={handleRemoveStream}
                    />
                  </div>
                ))}
              </div>
            </SortableContext>
          </DndContext>
        )}
      </div>
    </div>
  );
}
