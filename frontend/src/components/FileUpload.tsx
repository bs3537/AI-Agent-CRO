import { useRef, useState } from 'react'
import Button from '@mui/material/Button'
import UploadFileIcon from '@mui/icons-material/UploadFile'

// Upload button wrapping a hidden file input. Accepts the document types the
// backend extracts; calls onUpload with the chosen file and shows a spinner
// label while in flight.
export default function FileUpload({ onUpload }: { onUpload: (file: File) => Promise<void> }) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)

  // Send the selected file, then reset the input so the same file can be
  // re-picked later.
  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true)
    try {
      await onUpload(file)
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        hidden
        accept=".txt,.md,.markdown,.pdf,.docx"
        onChange={onPick}
      />
      <Button
        size="small"
        variant="outlined"
        startIcon={<UploadFileIcon />}
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? 'Uploading…' : 'Upload doc'}
      </Button>
    </>
  )
}
