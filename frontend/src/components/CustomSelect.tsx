import { useState, useRef, useEffect, useCallback, useId } from 'react';
import { createPortal } from 'react-dom';
import { useDropdownViewport } from '../hooks/useDropdownViewport';
import './CustomSelect.css';

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface CustomSelectProps {
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  searchable?: boolean;
  searchPlaceholder?: string;
  /** Accessible name for the trigger where no visible <label> can be associated
   *  — this is a custom listbox widget, not a native <select>. */
  ariaLabel?: string;
}

export function CustomSelect({
  options,
  value,
  onChange,
  placeholder = 'Select...',
  disabled = false,
  className = '',
  searchable = false,
  searchPlaceholder = 'Search...',
  ariaLabel,
}: CustomSelectProps) {
  const listboxId = `${useId()}-custom-select-listbox`;
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const optionsRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const selectedOption = options.find(opt => opt.value === value);

  const filteredOptions = searchable && searchQuery
    ? options.filter(opt =>
        opt.label.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : options;

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      const clickedInContainer = containerRef.current?.contains(target);
      const clickedInMenu = menuRef.current?.contains(target);
      if (!clickedInContainer && !clickedInMenu) {
        setIsOpen(false);
        setSearchQuery('');
        setHighlightedIndex(-1);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [isOpen]);

  const closeForUnavailablePlacement = useCallback(() => {
    setIsOpen(false);
    setSearchQuery('');
    setHighlightedIndex(-1);
  }, []);
  const menuPosition = useDropdownViewport({
    isOpen,
    triggerRef,
    desiredHeight: 250,
    // Search chrome plus one complete option; non-searchable menus need less,
    // but using the stronger bound keeps placement stable if search is toggled.
    minimumUsableHeight: searchable ? 88 : 40,
    onPlacementUnavailable: closeForUnavailablePlacement,
  });

  // Focus search input when dropdown opens
  useEffect(() => {
    if (isOpen && searchable && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [isOpen, searchable]);

  // Scroll highlighted option into view
  useEffect(() => {
    if (highlightedIndex >= 0 && optionsRef.current) {
      const optionElements = optionsRef.current.querySelectorAll('.custom-select-option');
      const highlightedElement = optionElements[highlightedIndex] as HTMLElement;
      if (highlightedElement) {
        highlightedElement.scrollIntoView({ block: 'nearest' });
      }
    }
  }, [highlightedIndex]);

  const handleToggle = useCallback(() => {
    if (disabled) return;
    setIsOpen(prev => !prev);
    if (isOpen) {
      setSearchQuery('');
      setHighlightedIndex(-1);
    }
  }, [disabled, isOpen]);

  const handleSelect = useCallback((optionValue: string) => {
    const option = options.find(opt => opt.value === optionValue);
    if (option?.disabled) return;

    onChange(optionValue);
    setIsOpen(false);
    setSearchQuery('');
    setHighlightedIndex(-1);
  }, [onChange, options]);

  const handleKeyDown = useCallback((event: React.KeyboardEvent) => {
    if (disabled) return;

    switch (event.key) {
      case 'Enter':
        event.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
        } else if (highlightedIndex >= 0 && filteredOptions[highlightedIndex]) {
          handleSelect(filteredOptions[highlightedIndex].value);
        }
        break;
      case ' ':
        // GH #489: a space typed in the search field must insert a literal
        // space, not select the highlighted option. handleKeyDown is bound on
        // the container, so keystrokes bubble up from the search input — only
        // treat Space as open/select when focus is on the trigger button.
        if (event.target === searchInputRef.current) break;
        event.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
        } else if (highlightedIndex >= 0 && filteredOptions[highlightedIndex]) {
          handleSelect(filteredOptions[highlightedIndex].value);
        }
        break;
      case 'Escape':
        event.preventDefault();
        setIsOpen(false);
        setSearchQuery('');
        setHighlightedIndex(-1);
        triggerRef.current?.focus();
        break;
      case 'ArrowDown':
        event.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
        } else {
          setHighlightedIndex(prev => {
            const nextIndex = prev + 1;
            // Skip disabled options
            let index = nextIndex;
            while (index < filteredOptions.length && filteredOptions[index]?.disabled) {
              index++;
            }
            return index < filteredOptions.length ? index : prev;
          });
        }
        break;
      case 'ArrowUp':
        event.preventDefault();
        if (isOpen) {
          setHighlightedIndex(prev => {
            const nextIndex = prev - 1;
            // Skip disabled options
            let index = nextIndex;
            while (index >= 0 && filteredOptions[index]?.disabled) {
              index--;
            }
            return index >= 0 ? index : prev;
          });
        }
        break;
      case 'Home':
        if (isOpen) {
          event.preventDefault();
          // Find first non-disabled option
          const firstEnabled = filteredOptions.findIndex(opt => !opt.disabled);
          setHighlightedIndex(firstEnabled >= 0 ? firstEnabled : 0);
        }
        break;
      case 'End':
        if (isOpen) {
          event.preventDefault();
          // Find last non-disabled option
          for (let i = filteredOptions.length - 1; i >= 0; i--) {
            if (!filteredOptions[i].disabled) {
              setHighlightedIndex(i);
              break;
            }
          }
        }
        break;
    }
  }, [disabled, isOpen, highlightedIndex, filteredOptions, handleSelect]);

  return (
    <div
      className={`custom-select ${className} ${disabled ? 'disabled' : ''} ${isOpen ? 'open' : ''}`}
      ref={containerRef}
      onKeyDown={handleKeyDown}
    >
      <button
        ref={triggerRef}
        type="button"
        className="custom-select-trigger"
        onClick={handleToggle}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={isOpen ? listboxId : undefined}
        aria-label={ariaLabel}
      >
        <span className="custom-select-value">
          {selectedOption ? selectedOption.label : placeholder}
        </span>
        <span className="material-icons custom-select-arrow">
          {isOpen ? 'expand_less' : 'expand_more'}
        </span>
      </button>

      {isOpen && createPortal(
        <div
          ref={menuRef}
          className="custom-select-menu"
          id={listboxId}
          role="listbox"
          style={{
            position: 'fixed',
            top: menuPosition.top,
            left: menuPosition.left,
            width: menuPosition.width,
            maxHeight: menuPosition.maxHeight,
            minWidth: 200,
          }}
        >
          {searchable && (
            <div className="custom-select-search">
              <span className="material-icons">search</span>
              <input
                ref={searchInputRef}
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setHighlightedIndex(0);
                }}
                placeholder={searchPlaceholder}
                aria-label="Search options"
                onClick={(e) => e.stopPropagation()}
              />
              {searchQuery && (
                <button
                  type="button"
                  className="clear-search"
                  onClick={(e) => {
                    e.stopPropagation();
                    setSearchQuery('');
                    searchInputRef.current?.focus();
                  }}
                  aria-label="Clear search"
                  title="Clear search"
                >
                  <span className="material-icons" aria-hidden="true">close</span>
                </button>
              )}
            </div>
          )}

          <div className="custom-select-options" ref={optionsRef}>
            {filteredOptions.length === 0 ? (
              <div className="custom-select-no-results">No options found</div>
            ) : (
              filteredOptions.map((option, index) => (
                <div
                  key={option.value}
                  className={`custom-select-option ${option.value === value ? 'selected' : ''} ${option.disabled ? 'disabled' : ''} ${index === highlightedIndex ? 'highlighted' : ''}`}
                  onClick={() => handleSelect(option.value)}
                  role="option"
                  aria-selected={option.value === value}
                  aria-disabled={option.disabled}
                >
                  {option.label}
                </div>
              ))
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
