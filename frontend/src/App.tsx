import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'

const API_BASE = '/api/v1'

interface Version {
  id: number
  name: string
  description: string
  created_at: string
  user_id: string
  is_published: boolean
}

interface UploadResult {
  success: boolean
  total_files: number
  modules_count: number
  registers_count: number
  warnings: string[]
  errors: string[]
  html_url: string | null
  top_addrmap_name: string | null
  uninstantiated_modules: {
    name: string
    source: string
    reason: string
    start_addr: number
    register_count: number
  }[]
  code_results: {
    modules?: Record<string, Record<string, { success: boolean; path?: string; error?: string }>>
    combined?: Record<string, { success: boolean; path?: string; error?: string }>
  }
}

interface UninstantiatedModule {
  name: string
  start_addr: number
  end_addr: number
  size: number
  register_count: number
  reason: string
}

interface GeneratedFiles {
  version_id: number
  version_name: string
  modules: Record<string, Record<string, { name: string; path: string; size: number }>>
  combined: Record<string, { name: string; path: string; size: number }>
  html: { exists: boolean; url: string | null }
}

interface RTLOptions {
  cpu_interfaces: { value: string; label: string; default: boolean }[]
  address_widths: { value: number; label: string; default: boolean }[]
  reset_types: { value: string; label: string; default: boolean }[]
  modules: { name: string; register_count: number; is_array: boolean }[]
}

interface RTLFile {
  filename: string
  path: string
  size: number
  modified: number
}

export default function App() {
  const [versions, setVersions] = useState<Version[]>([])
  const [selectedVersion, setSelectedVersion] = useState<Version | null>(null)
  const [newVersionName, setNewVersionName] = useState('')
  const [newVersionDesc, setNewVersionDesc] = useState('')
  const [uploadFiles, setUploadFiles] = useState<FileList | null>(null)
  const [message, setMessage] = useState('')
  const [messageType, setMessageType] = useState<'info' | 'error' | 'success'>('info')
  const [uploadErrors, setUploadErrors] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [htmlUrl, setHtmlUrl] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'upload' | 'download'>('upload')
  const [generatedFiles, setGeneratedFiles] = useState<GeneratedFiles | null>(null)
  const [selectedModule, setSelectedModule] = useState<string>('')
  const [rtlOptions, setRtlOptions] = useState<RTLOptions | null>(null)
  const [rtlFiles, setRtlFiles] = useState<RTLFile[]>([])
  const [rtlConfig, setRtlConfig] = useState({
    cpu_if: 'axilite',
    address_width: 32,
    reset_type: 'active_low'
  })
  const [generatingRTL, setGeneratingRTL] = useState(false)
  const [showRTLDownload, setShowRTLDownload] = useState(false)
  const [uninstantiatedModules, setUninstantiatedModules] = useState<UninstantiatedModule[]>([])
  const [topAddrMapName, setTopAddrMapName] = useState<string>('')

  // Multi-user state
  const [currentUser, setCurrentUser] = useState<string>('')
  const [isAdmin, setIsAdmin] = useState<boolean>(false)
  const [deleteConfirmVersion, setDeleteConfirmVersion] = useState<Version | null>(null)
  const [deleteConfirmInput, setDeleteConfirmInput] = useState<string>('')
  const [showAdminPassword, setShowAdminPassword] = useState<boolean>(false)
  const [adminPasswordInput, setAdminPasswordInput] = useState<string>('')

  const fetchVersions = useCallback(async () => {
    try {
      const userParam = isAdmin ? 'admin' : currentUser
      const response = await axios.get(`${API_BASE}/versions`, {
        params: userParam ? { user: userParam } : {}
      })
      setVersions(response.data)
    } catch (error) {
      showMessage('Failed to fetch versions', 'error')
    }
  }, [currentUser, isAdmin])

  useEffect(() => {
    fetchVersions()
  }, [fetchVersions])

  const showMessage = (msg: string, type: 'info' | 'error' | 'success' = 'info') => {
    setMessage(msg)
    setMessageType(type)
    setTimeout(() => setMessage(''), 5000)
  }

  const handleUserInput = (value: string) => {
    const cleaned = value.replace(/[^a-zA-Z]/g, '')
    if (cleaned === 'admin') {
      setCurrentUser('admin')
      setShowAdminPassword(true)
    } else {
      setCurrentUser(cleaned)
      setIsAdmin(false)
      setShowAdminPassword(false)
      setAdminPasswordInput('')
    }
  }

  const handleAdminLogin = () => {
    if (adminPasswordInput === 'askcp') {
      setIsAdmin(true)
      setShowAdminPassword(false)
      setAdminPasswordInput('')
      showMessage('Admin logged in', 'success')
    } else {
      showMessage('Invalid admin password', 'error')
    }
  }

  const createVersion = async () => {
    if (!newVersionName.trim()) {
      showMessage('Please enter version name', 'error')
      return
    }
    if (!currentUser) {
      showMessage('Please enter a username first', 'error')
      return
    }
    try {
      await axios.post(`${API_BASE}/versions`, {
        name: newVersionName,
        description: newVersionDesc,
        user_id: currentUser
      })
      setNewVersionName('')
      setNewVersionDesc('')
      showMessage('Version created successfully', 'success')
      fetchVersions()
    } catch (error: any) {
      showMessage(error.response?.data?.detail || 'Failed to create version', 'error')
    }
  }

  const canModifyVersion = (v: Version) => {
    return currentUser && v.user_id === currentUser && !v.is_published
  }

  const canDeleteVersion = (v: Version) => {
    if (!currentUser) return false
    if (isAdmin) return true
    return v.user_id === currentUser
  }

  const canPublishVersion = (v: Version) => {
    return currentUser && v.user_id === currentUser && !v.is_published
  }

  const openDeleteConfirm = (v: Version, e: React.MouseEvent) => {
    e.stopPropagation()
    setDeleteConfirmVersion(v)
    setDeleteConfirmInput('')
  }

  const closeDeleteConfirm = () => {
    setDeleteConfirmVersion(null)
    setDeleteConfirmInput('')
  }

  const executeDelete = async () => {
    if (!deleteConfirmVersion) return
    const v = deleteConfirmVersion
    const password = deleteConfirmInput
    const request_user_id = isAdmin ? 'admin' : currentUser

    try {
      await axios.delete(`${API_BASE}/versions/${v.id}`, {
        data: { password, user_id: request_user_id }
      })
      showMessage('Version deleted', 'success')
      if (selectedVersion?.id === v.id) {
        setSelectedVersion(null)
        setHtmlUrl(null)
        setGeneratedFiles(null)
      }
      setDeleteConfirmVersion(null)
      setDeleteConfirmInput('')
      fetchVersions()
    } catch (error: any) {
      showMessage(error.response?.data?.detail || 'Failed to delete version', 'error')
    }
  }

  const publishVersion = async (v: Version, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm(`Publish version "${v.name}"? This will make it read-only.`)) return
    try {
      await axios.post(`${API_BASE}/versions/${v.id}/publish`, {
        user_id: currentUser
      })
      showMessage('Version published successfully', 'success')
      fetchVersions()
    } catch (error: any) {
      showMessage(error.response?.data?.detail || 'Failed to publish version', 'error')
    }
  }

  const fetchGeneratedFiles = async (versionId: number) => {
    try {
      const response = await axios.get(`${API_BASE}/versions/${versionId}/files`)
      setGeneratedFiles(response.data)
      // Set first module as default if none selected
      const modules = Object.keys(response.data.modules || {})
      if (modules.length > 0 && !selectedModule) {
        setSelectedModule(modules[0])
      }
      // Also fetch RTL options
      await fetchRTLOptions(versionId)
    } catch (error) {
      console.error('Failed to fetch files:', error)
    }
  }

  const fetchRTLOptions = async (versionId: number) => {
    try {
      const response = await axios.get(`${API_BASE}/versions/${versionId}/rtl/options`)
      setRtlOptions(response.data)
      // Set defaults
      const defaultCpu = response.data.cpu_interfaces?.find((c: any) => c.default)?.value || 'axilite'
      const defaultAddr = response.data.address_widths?.find((a: any) => a.default)?.value || 32
      setRtlConfig(prev => ({
        ...prev,
        cpu_if: defaultCpu,
        address_width: defaultAddr
      }))
    } catch (error) {
      console.error('Failed to fetch RTL options:', error)
    }
  }

  const fetchRTLFiles = async (versionId: number, module?: string) => {
    try {
      const url = module
        ? `${API_BASE}/versions/${versionId}/rtl/files?module=${module}`
        : `${API_BASE}/versions/${versionId}/rtl/files`
      const response = await axios.get(url)
      if (response.data.generated) {
        setRtlFiles(response.data.files)
        setShowRTLDownload(true)
      } else {
        setRtlFiles([])
        setShowRTLDownload(false)
      }
    } catch (error) {
      console.error('Failed to fetch RTL files:', error)
      setRtlFiles([])
      setShowRTLDownload(false)
    }
  }

  const generateRTL = async () => {
    if (!selectedVersion) return
    setGeneratingRTL(true)
    try {
      const response = await axios.post(
        `${API_BASE}/versions/${selectedVersion.id}/rtl/generate`,
        {
          module: selectedModule || null,
          cpu_if: rtlConfig.cpu_if,
          address_width: rtlConfig.address_width,
          reset_type: rtlConfig.reset_type
        }
      )
      if (response.data.success) {
        showMessage(`RTL generated successfully! ${response.data.files?.length || 0} files created.`, 'success')
        await fetchRTLFiles(selectedVersion.id, selectedModule || undefined)
      } else {
        showMessage('RTL generation failed: ' + response.data.message, 'error')
      }
    } catch (error: any) {
      showMessage(error.response?.data?.detail || 'RTL generation failed', 'error')
    } finally {
      setGeneratingRTL(false)
    }
  }

  const downloadRTL = async (filename?: string) => {
    if (!selectedVersion) return
    try {
      const url = filename
        ? `${API_BASE}/versions/${selectedVersion.id}/rtl/download?module=${selectedModule}&file=${filename}`
        : `${API_BASE}/versions/${selectedVersion.id}/rtl/download?module=${selectedModule}`
      const response = await axios.get(url, { responseType: 'blob' })
      const blobUrl = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement('a')
      link.href = blobUrl
      link.setAttribute('download', filename || `${selectedModule || selectedVersion.name}_rtl.zip`)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(blobUrl)
    } catch (error: any) {
      showMessage(error.response?.data?.detail || 'Download failed', 'error')
    }
  }

  const uploadExcel = async () => {
    if (!selectedVersion || !uploadFiles || uploadFiles.length === 0) {
      showMessage('Please select version and files', 'error')
      return
    }
    // Clear previous errors when starting new upload
    setUploadErrors([])
    setLoading(true)

    const formData = new FormData()
    for (let i = 0; i < uploadFiles.length; i++) {
      formData.append('files', uploadFiles[i])
    }

    try {
      const response = await axios.post(
        `${API_BASE}/versions/${selectedVersion.id}/upload/batch`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )

      const data: UploadResult = response.data
      if (data.html_url) {
        setHtmlUrl(data.html_url)
      }

      // Update top addrmap name
      if (data.top_addrmap_name) {
        setTopAddrMapName(data.top_addrmap_name)
      }

      // Update uninstantiated modules
      if (data.uninstantiated_modules) {
        setUninstantiatedModules(data.uninstantiated_modules.map(um => ({
          name: um.name,
          start_addr: um.start_addr,
          end_addr: 0,
          size: 0,
          register_count: um.register_count,
          reason: um.reason
        })))
      }

      // Refresh file list
      await fetchGeneratedFiles(selectedVersion.id)

      let messageText = `Upload successful! ${data.total_files} files, ${data.modules_count} modules, ${data.registers_count} registers.`

      if (data.top_addrmap_name) {
        messageText += `\nTop addrmap: ${data.top_addrmap_name}`
      }

      if (data.uninstantiated_modules?.length > 0) {
        messageText += `\n⚠️ ${data.uninstantiated_modules.length} uninstantiated modules (check Uninstantiated Modules section)`
      }

      const warningsText = data.warnings?.length > 0
        ? `\nWarnings: ${data.warnings.slice(0, 3).join(', ')}${data.warnings.length > 3 ? '...' : ''}`
        : ''

      showMessage(messageText + warningsText, 'success')
      setUploadFiles(null)
    } catch (error: any) {
      const detail = error.response?.data?.detail
      if (typeof detail === 'object' && detail.errors) {
        setUploadErrors(detail.errors)
        showMessage(`Upload failed with ${detail.errors.length} error(s)`, 'error')
      } else {
        const errorMsg = detail || error.message
        setUploadErrors([errorMsg])
        showMessage(`Upload failed: ${errorMsg}`, 'error')
      }
    } finally {
      setLoading(false)
    }
  }

  const downloadFile = async (formatType: string, module?: string) => {
    if (!selectedVersion) {
      showMessage('Please select a version first', 'error')
      return
    }
    try {
      const url = module
        ? `${API_BASE}/versions/${selectedVersion.id}/download/${formatType}?module=${module}`
        : `${API_BASE}/versions/${selectedVersion.id}/download/${formatType}`

      const response = await axios.get(url, { responseType: 'blob' })
      const blobUrl = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement('a')
      link.href = blobUrl

      // Determine filename
      const extMap: Record<string, string> = {
        rdl: 'rdl',
        ralf: 'ralf',
        header: 'h',
        svheader: 'svh',
        uvm: 'sv'
      }
      const ext = extMap[formatType] || formatType
      const filename = module ? `${module}.${ext}` : `${selectedVersion.name}.${ext}`

      link.setAttribute('download', filename)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(blobUrl)
    } catch (error: any) {
      showMessage(error.response?.data?.detail || 'Download failed', 'error')
    }
  }

  const handleVersionSelect = (v: Version) => {
    setSelectedVersion(v)
    setHtmlUrl(null)
    setSelectedModule('')
    setRtlFiles([])
    setShowRTLDownload(false)
    setUninstantiatedModules([])
    setTopAddrMapName('')
    fetchGeneratedFiles(v.id)
    fetchUninstantiatedModules(v.id)
  }

  const fetchUninstantiatedModules = async (versionId: number) => {
    try {
      const response = await axios.get(`${API_BASE}/versions/${versionId}/uninstantiated`)
      if (response.data.modules) {
        setUninstantiatedModules(response.data.modules)
      }
    } catch (error) {
      console.error('Failed to fetch uninstantiated modules:', error)
    }
  }

  const instantiateModule = async (moduleName: string, parentModule: string) => {
    if (!selectedVersion) return
    try {
      await axios.post(
        `${API_BASE}/versions/${selectedVersion.id}/instantiate-module`,
        new URLSearchParams({
          module_name: moduleName,
          parent_module: parentModule
        })
      )
      showMessage(`Module ${moduleName} instantiated under ${parentModule}`, 'success')
      fetchUninstantiatedModules(selectedVersion.id)
      fetchGeneratedFiles(selectedVersion.id)
    } catch (error: any) {
      showMessage(error.response?.data?.detail || 'Failed to instantiate module', 'error')
    }
  }

  const handleModuleSelect = (moduleName: string) => {
    setSelectedModule(moduleName)
    setRtlFiles([])
    setShowRTLDownload(false)
    if (selectedVersion) {
      fetchRTLFiles(selectedVersion.id, moduleName || undefined)
    }
  }

  const deleteHint = isAdmin ? 'Enter "askcp" as password' : 'Enter your username as password'

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <h1 style={styles.title}>Register Description Tool</h1>
        <p style={styles.subtitle}>Generate register documentation and code from Excel files</p>
      </header>

      {/* User Navigation Bar */}
      <div style={styles.userBar}>
        <div style={styles.userBarInner}>
          <div style={styles.userInputGroup}>
            <label style={styles.userLabel}>User:</label>
            <input
              style={styles.userInput}
              placeholder="Enter username (letters only)"
              value={currentUser}
              onChange={(e) => handleUserInput(e.target.value)}
              maxLength={20}
            />
            {isAdmin && <span style={styles.adminBadge}>ADMIN</span>}
            {currentUser && !isAdmin && currentUser !== 'admin' && (
              <span style={styles.userBadge}>{currentUser}</span>
            )}
          </div>
          {currentUser === 'admin' && !isAdmin && (
            <span style={styles.adminPending}>Password required</span>
          )}
        </div>
      </div>

      {message && (
        <div style={{...styles.message, ...styles[`message${messageType}`]}} onClick={() => setMessage('')}>
          {message}
        </div>
      )}

      {/* Admin Password Modal */}
      {showAdminPassword && (
        <div style={styles.modalOverlay} onClick={() => setShowAdminPassword(false)}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h3 style={styles.modalTitle}>Admin Login</h3>
            <p style={styles.modalText}>Enter admin password to continue</p>
            <input
              style={styles.modalInput}
              type="password"
              placeholder="Password"
              value={adminPasswordInput}
              onChange={(e) => setAdminPasswordInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleAdminLogin()}
              autoFocus
            />
            <div style={styles.modalActions}>
              <button style={styles.modalBtnSecondary} onClick={() => setShowAdminPassword(false)}>Cancel</button>
              <button style={styles.modalBtnPrimary} onClick={handleAdminLogin}>Login</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteConfirmVersion && (
        <div style={styles.modalOverlay} onClick={closeDeleteConfirm}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h3 style={{...styles.modalTitle, color: '#ff6b6b'}}>Delete Version</h3>
            <div style={styles.modalInfo}>
              <p><strong>Name:</strong> {deleteConfirmVersion.name}</p>
              <p><strong>Owner:</strong> {deleteConfirmVersion.user_id}</p>
              <p><strong>Created:</strong> {new Date(deleteConfirmVersion.created_at).toLocaleString()}</p>
            </div>
            <input
              style={styles.modalInput}
              type="password"
              placeholder={deleteHint}
              value={deleteConfirmInput}
              onChange={(e) => setDeleteConfirmInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && executeDelete()}
              autoFocus
            />
            <div style={styles.modalActions}>
              <button style={styles.modalBtnSecondary} onClick={closeDeleteConfirm}>Cancel</button>
              <button style={styles.modalBtnDanger} onClick={executeDelete}>Delete</button>
            </div>
          </div>
        </div>
      )}

      <div style={styles.grid}>
        {/* Version Management Panel */}
        <div style={styles.card}>
          <h2 style={styles.cardTitle}>Version Management</h2>

          <div style={styles.inputGroup}>
            <input
              style={styles.input}
              placeholder="Version name (e.g., v0.8)"
              value={newVersionName}
              onChange={(e) => setNewVersionName(e.target.value)}
            />
            <input
              style={styles.input}
              placeholder="Description (optional)"
              value={newVersionDesc}
              onChange={(e) => setNewVersionDesc(e.target.value)}
            />
            <button style={styles.button} onClick={createVersion} disabled={!currentUser}>
              Create Version
            </button>
            {!currentUser && (
              <p style={{...styles.hintText, color: '#e74c3c', fontSize: '0.85em'}}>
                Please enter a username to create versions
              </p>
            )}
          </div>

          <div style={styles.versionList}>
            {versions.map((v) => (
              <div
                key={v.id}
                style={{
                  ...styles.versionItem,
                  ...(selectedVersion?.id === v.id ? styles.versionItemActive : {}),
                  ...(v.is_published ? styles.versionItemPublished : {})
                }}
                onClick={() => handleVersionSelect(v)}
              >
                <div style={styles.versionInfo}>
                  <div style={styles.versionNameRow}>
                    <strong>{v.name}</strong>
                    {v.is_published && <span style={styles.publishedBadge}>PUBLISHED</span>}
                    {!v.is_published && <span style={styles.unpublishedBadge}>DRAFT</span>}
                  </div>
                  <span style={styles.versionDesc}>{v.description}</span>
                  <span style={styles.versionMeta}>
                    by {v.user_id} | {new Date(v.created_at).toLocaleDateString()}
                  </span>
                </div>
                <div style={styles.versionActions}>
                  {canPublishVersion(v) && (
                    <button
                      style={styles.publishBtn}
                      onClick={(e) => publishVersion(v, e)}
                      title="Publish version"
                    >
                      Publish
                    </button>
                  )}
                  {canDeleteVersion(v) && (
                    <button
                      style={styles.deleteBtn}
                      onClick={(e) => openDeleteConfirm(v, e)}
                      title="Delete version"
                    >
                      ×
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Upload / Download Panel */}
        <div style={styles.card}>
          <div style={styles.tabHeader}>
            <button
              style={{...styles.tab, ...(activeTab === 'upload' ? styles.tabActive : {})}}
              onClick={() => setActiveTab('upload')}
            >
              Upload Excel & RALF
            </button>
            <button
              style={{...styles.tab, ...(activeTab === 'download' ? styles.tabActive : {})}}
              onClick={() => setActiveTab('download')}
            >
              Download Code
            </button>
          </div>

          {activeTab === 'upload' ? (
            <div style={styles.uploadArea}>
              <p>Current version: <strong>{selectedVersion?.name || 'Not selected'}</strong></p>
              {selectedVersion?.is_published && (
                <p style={{color: '#ffc107', fontSize: '0.9em'}}>
                  This version is published and read-only. Upload is disabled.
                </p>
              )}
              {selectedVersion && !canModifyVersion(selectedVersion) && !selectedVersion.is_published && (
                <p style={{color: '#e74c3c', fontSize: '0.9em'}}>
                  You can only upload to your own unpublished versions.
                </p>
              )}
              <input
                type="file"
                accept=".xls,.xlsx,.ralf"
                multiple
                onChange={(e) => setUploadFiles(e.target.files)}
                style={styles.fileInput}
                disabled={!selectedVersion || !canModifyVersion(selectedVersion)}
              />
              {uploadFiles && uploadFiles.length > 0 && (
                <div style={styles.fileList}>
                  <p>Selected {uploadFiles.length} files:</p>
                  {Array.from(uploadFiles).map((f, i) => (
                    <span key={i} style={styles.fileTag}>{f.name}</span>
                  ))}
                </div>
              )}
              {/* Hint message when button is disabled */}
              {!selectedVersion && (
                <p style={{...styles.hintText, color: '#e74c3c'}}>
                  ⚠️ Please select a version first
                </p>
              )}
              {selectedVersion && (!uploadFiles || uploadFiles.length === 0) && (
                <p style={{...styles.hintText, color: '#f39c12'}}>
                  ⚠️ Please select Excel or RALF files to upload
                </p>
              )}

              <button
                style={{
                  ...styles.button,
                  ...styles.buttonPrimary,
                  ...(loading || !uploadFiles?.length || !selectedVersion || !canModifyVersion(selectedVersion) ? styles.buttonDisabled : {})
                }}
                onClick={uploadExcel}
                disabled={loading || !uploadFiles?.length || !selectedVersion || !canModifyVersion(selectedVersion)}
              >
                {loading ? 'Processing...' : `Upload & Generate`}
              </button>

              {/* Error Display Box */}
              {uploadErrors.length > 0 && (
                <div style={styles.errorBox}>
                  <div style={styles.errorBoxHeader}>
                    <span style={styles.errorBoxTitle}>❌ Upload Errors ({uploadErrors.length})</span>
                    <button
                      style={styles.errorBoxClose}
                      onClick={() => setUploadErrors([])}
                      title="Clear errors"
                    >
                      ×
                    </button>
                  </div>
                  <div style={styles.errorBoxContent}>
                    {uploadErrors.map((err, idx) => (
                      <div key={idx} style={styles.errorBoxItem}>
                        <span style={styles.errorBoxNumber}>{idx + 1}.</span>
                        <span style={styles.errorBoxText}>{err}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div style={styles.downloadArea}>
              <p>Current version: <strong>{selectedVersion?.name || 'Not selected'}</strong></p>

              {generatedFiles && (Object.keys(generatedFiles.modules || {}).length > 0 || Object.keys(generatedFiles.combined || {}).length > 0) ? (
                <>
                  {/* Module Selector - only show if modules exist */}
                  {Object.keys(generatedFiles.modules || {}).length > 0 && (
                    <div style={styles.moduleSelector}>
                      <label style={styles.selectLabel}>Select Module:</label>
                      <select
                        style={styles.select}
                        value={selectedModule}
                        onChange={(e) => handleModuleSelect(e.target.value)}
                      >
                        <option value="">-- Combined (All Modules) --</option>
                        {Object.keys(generatedFiles.modules).map((moduleName) => (
                          <option key={moduleName} value={moduleName}>{moduleName}</option>
                        ))}
                      </select>
                    </div>
                  )}

                  {/* Download Buttons */}
                  <div style={styles.downloadSection}>
                    <h4 style={styles.downloadTitle}>
                      {selectedModule ? `Files for ${selectedModule}` : 'Combined Files (All Modules)'}
                    </h4>
                    <div style={styles.downloadButtons}>
                      {['rdl', 'ralf', 'header', 'svheader', 'uvm'].map((fmt) => {
                        const files = selectedModule
                          ? generatedFiles.modules[selectedModule]
                          : generatedFiles.combined
                        const fileInfo = files?.[fmt]
                        const extMap: Record<string, string> = {
                          rdl: '.rdl',
                          ralf: '.ralf',
                          header: '.h',
                          svheader: '.svh',
                          uvm: '.sv'
                        }
                        const labelMap: Record<string, string> = {
                          rdl: 'RDL',
                          ralf: 'RALF',
                          header: 'C Header',
                          svheader: 'SV Header',
                          uvm: 'UVM RegModel'
                        }

                        return (
                          <button
                            key={fmt}
                            style={{
                              ...styles.downloadBtn,
                              ...(fileInfo ? styles.downloadBtnAvailable : styles.downloadBtnUnavailable)
                            }}
                            onClick={() => downloadFile(fmt, selectedModule || undefined)}
                            disabled={!fileInfo}
                          >
                            {labelMap[fmt]} {extMap[fmt]}
                            {fileInfo && <span style={styles.fileSize}> ({(fileInfo.size / 1024).toFixed(1)} KB)</span>}
                          </button>
                        )
                      })}
                    </div>
                  </div>

                  {/* RTL Generation Section */}
                  <div style={styles.rtlSection}>
                    <h4 style={styles.downloadTitle}>
                      <span style={styles.rtlTitleIcon}>⚡</span>
                      RTL Code Generation
                    </h4>
                    <p style={styles.rtlDescription}>
                      Generate SystemVerilog RTL register block with APB/AXI interface
                    </p>

                    {/* RTL Configuration */}
                    <div style={styles.rtlConfig}>
                      <div style={styles.rtlConfigRow}>
                        <label style={styles.rtlLabel}>CPU Interface:</label>
                        <select
                          style={styles.rtlSelect}
                          value={rtlConfig.cpu_if}
                          onChange={(e) => setRtlConfig({...rtlConfig, cpu_if: e.target.value})}
                        >
                          {rtlOptions?.cpu_interfaces?.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          )) || (
                            <>
                              <option value="axilite">AXI4-Lite</option>
                              <option value="apb3">APB3</option>
                              <option value="apb4">APB4</option>
                            </>
                          )}
                        </select>
                      </div>

                      <div style={styles.rtlConfigRow}>
                        <label style={styles.rtlLabel}>Address Width:</label>
                        <select
                          style={styles.rtlSelect}
                          value={rtlConfig.address_width}
                          onChange={(e) => setRtlConfig({...rtlConfig, address_width: parseInt(e.target.value)})}
                        >
                          {rtlOptions?.address_widths?.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          )) || (
                            <>
                              <option value={16}>16-bit</option>
                              <option value={32}>32-bit</option>
                              <option value={64}>64-bit</option>
                            </>
                          )}
                        </select>
                      </div>
                    </div>

                    {/* Generate Button */}
                    <button
                      style={styles.rtlGenerateBtn}
                      onClick={generateRTL}
                      disabled={generatingRTL}
                    >
                      {generatingRTL ? 'Generating...' : `Generate RTL ${selectedModule ? `for ${selectedModule}` : '(All Modules)'}`}
                    </button>

                    {/* Generated RTL Files */}
                    {showRTLDownload && rtlFiles.length > 0 && (
                      <div style={styles.rtlFilesSection}>
                        <h5 style={styles.rtlFilesTitle}>Generated RTL Files:</h5>
                        <div style={styles.rtlFilesList}>
                          {rtlFiles.map((file) => (
                            <div key={file.filename} style={styles.rtlFileItem}>
                              <span style={styles.rtlFileName}>{file.filename}</span>
                              <span style={styles.rtlFileSize}>({(file.size / 1024).toFixed(1)} KB)</span>
                              <button
                                style={styles.rtlFileDownloadBtn}
                                onClick={() => downloadRTL(file.filename)}
                              >
                                Download
                              </button>
                            </div>
                          ))}
                        </div>
                        <button
                          style={styles.rtlDownloadAllBtn}
                          onClick={() => downloadRTL()}
                        >
                          Download All (ZIP)
                        </button>
                      </div>
                    )}
                  </div>

                  {/* Module Summary */}
                  {Object.keys(generatedFiles.modules).length > 0 && (
                    <div style={styles.moduleSummary}>
                      <h4 style={styles.downloadTitle}>Generated Modules</h4>
                      <div style={styles.moduleList}>
                        {Object.entries(generatedFiles.modules).map(([name, files]) => {
                          const fileCount = Object.keys(files).length
                          return (
                            <div
                              key={name}
                              style={{
                                ...styles.moduleItem,
                                ...(selectedModule === name ? styles.moduleItemActive : {})
                              }}
                            >
                              <span style={styles.moduleName} onClick={() => handleModuleSelect(name)}>{name}</span>
                              <span style={styles.moduleFileCount}>{fileCount} files</span>
                              {(htmlUrl || generatedFiles?.html?.exists) && (
                                <a
                                  href={`${window.location.origin}${htmlUrl || generatedFiles?.html?.url}#${name}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  style={styles.moduleRegLink}
                                  title="View registers"
                                >
                                  📋
                                </a>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <p style={styles.noFilesMessage}>
                  No files generated yet. Please upload Excel files first.
                </p>
              )}

              <p style={styles.downloadNote}>
                Note: UVM and RTL files are generated in the output directory when PeakRDL is installed.
              </p>
            </div>
          )}
        </div>

        {/* HTML View Link */}
        {(htmlUrl || generatedFiles?.html?.exists) && (
          <div style={styles.card}>
            <h2 style={styles.cardTitle}>Generated Webpage</h2>
            <div style={styles.htmlLinkContainer}>
              <p style={styles.htmlLinkText}>Register documentation webpage is ready:</p>
              <a
                href={`${window.location.origin}${htmlUrl || generatedFiles?.html?.url}`}
                target="_blank"
                rel="noopener noreferrer"
                style={styles.htmlLink}
              >
                View {selectedVersion?.name} Register Map →
              </a>
            </div>
          </div>
        )}

        {/* Uninstantiated Modules */}
        {uninstantiatedModules.length > 0 && (
          <div style={{...styles.card, border: '2px solid rgba(255, 193, 7, 0.5)'}}>
            <h2 style={{...styles.cardTitle, color: '#ffc107'}}>
              ⚠️ Uninstantiated Modules ({uninstantiatedModules.length})
            </h2>
            <p style={{color: '#aaa', marginBottom: '15px', fontSize: '0.9em'}}>
              These modules could not be automatically instantiated in the hierarchy.
              You can manually assign them to a parent module.
            </p>
            <div style={styles.uninstantiatedList}>
              {uninstantiatedModules.map((mod) => (
                <div key={mod.name} style={styles.uninstantiatedItem}>
                  <div style={styles.uninstantiatedInfo}>
                    <strong style={{color: '#ffc107'}}>{mod.name}</strong>
                    <span style={styles.uninstantiatedMeta}>
                      @0x{mod.start_addr?.toString(16).toUpperCase() || '0'} | {mod.register_count} registers
                    </span>
                    <span style={styles.uninstantiatedReason}>{mod.reason}</span>
                  </div>
                  <select
                    style={styles.uninstantiatedSelect}
                    onChange={(e) => {
                      if (e.target.value) {
                        instantiateModule(mod.name, e.target.value)
                        e.target.value = ''
                      }
                    }}
                    defaultValue=""
                  >
                    <option value="">Assign to parent...</option>
                    {generatedFiles && Object.keys(generatedFiles.modules || {}).map((name) => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Top AddrMap Info */}
        {topAddrMapName && (
          <div style={styles.card}>
            <h2 style={styles.cardTitle}>Hierarchy Info</h2>
            <div style={styles.hierarchyInfo}>
              <div style={styles.hierarchyItem}>
                <span style={styles.hierarchyLabel}>Top AddrMap:</span>
                <span style={styles.hierarchyValue}>{topAddrMapName}</span>
              </div>
              {generatedFiles && (
                <div style={styles.hierarchyItem}>
                  <span style={styles.hierarchyLabel}>Total Modules:</span>
                  <span style={styles.hierarchyValue}>
                    {Object.keys(generatedFiles.modules || {}).length}
                  </span>
                </div>
              )}
              {uninstantiatedModules.length > 0 && (
                <div style={styles.hierarchyItem}>
                  <span style={styles.hierarchyLabel}>Uninstantiated:</span>
                  <span style={{...styles.hierarchyValue, color: '#ffc107'}}>
                    {uninstantiatedModules.length}
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Quick Help */}
        <div style={styles.card}>
          <h2 style={styles.cardTitle}>Quick Help</h2>
          <div style={styles.helpContent}>
            <p><strong>Input Formats:</strong></p>
            <ul style={styles.helpList}>
              <li><strong>Excel:</strong> addr_map sheet (MODULE_NAME, start_addr, end_addr, size) + register sheet (OffsetAddress, RegName, Width, Bits, FieldName, Access, ResetValue)</li>
              <li><strong>RALF:</strong> UVM Register Abstraction Layer format (.ralf files)</li>
            </ul>
            <p><strong>Output Formats:</strong></p>
            <ul style={styles.helpList}>
              <li>HTML: Interactive register browser</li>
              <li>RDL: SystemRDL 2.0 format</li>
              <li>RALF: UVM Register Abstraction Layer format</li>
              <li>C Header: Register defines for firmware</li>
              <li>SV Header: SystemVerilog defines</li>
              <li>RTL: Generate SystemVerilog register block with AXI4-Lite/APB interface</li>
            </ul>
            <p style={{marginTop: '15px'}}><strong>RTL Generation:</strong></p>
            <ul style={styles.helpList}>
              <li>Select a module or use "Combined" for all modules</li>
              <li>Choose CPU interface (AXI4-Lite, APB3, APB4)</li>
              <li>Set address width (16/32/64-bit)</li>
              <li>Click "Generate RTL" button</li>
              <li>Download generated .sv files</li>
            </ul>
          </div>
        </div>
      </div>

      <footer style={styles.footer}>
        <p>Register Description Tool v1.0 | Cross-platform: Windows & Linux</p>
      </footer>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: '100vh',
    background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)',
    padding: '20px',
    color: '#e0e0e0',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  },
  header: {
    textAlign: 'center',
    padding: '40px 20px',
  },
  title: {
    fontSize: '2.5em',
    background: 'linear-gradient(90deg, #00d4ff, #7b2cbf)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
    marginBottom: '10px',
  },
  subtitle: {
    color: '#888',
    fontSize: '1.1em',
  },
  userBar: {
    maxWidth: '1400px',
    margin: '0 auto 20px',
    padding: '0 20px',
  },
  userBarInner: {
    display: 'flex',
    alignItems: 'center',
    gap: '15px',
    background: 'rgba(255,255,255,0.05)',
    borderRadius: '12px',
    padding: '12px 20px',
    border: '1px solid rgba(255,255,255,0.1)',
  },
  userInputGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    flex: 1,
  },
  userLabel: {
    color: '#00d4ff',
    fontWeight: 'bold',
    fontSize: '0.95em',
  },
  userInput: {
    padding: '8px 12px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.2)',
    borderRadius: '6px',
    color: '#fff',
    fontSize: '0.95em',
    width: '200px',
  },
  userBadge: {
    padding: '4px 10px',
    background: 'rgba(0,212,255,0.2)',
    borderRadius: '4px',
    color: '#00d4ff',
    fontSize: '0.85em',
    fontWeight: 'bold',
  },
  adminBadge: {
    padding: '4px 10px',
    background: 'rgba(220,53,69,0.3)',
    borderRadius: '4px',
    color: '#ff6b6b',
    fontSize: '0.85em',
    fontWeight: 'bold',
  },
  adminPending: {
    color: '#f39c12',
    fontSize: '0.85em',
    fontStyle: 'italic',
  },
  message: {
    borderRadius: '8px',
    padding: '15px 20px',
    marginBottom: '20px',
    cursor: 'pointer',
    whiteSpace: 'pre-line',
  },
  messageinfo: {
    background: 'rgba(0,212,255,0.1)',
    border: '1px solid rgba(0,212,255,0.3)',
  },
  messageerror: {
    background: 'rgba(220,53,69,0.2)',
    border: '1px solid rgba(220,53,69,0.5)',
  },
  messagesuccess: {
    background: 'rgba(40,167,69,0.2)',
    border: '1px solid rgba(40,167,69,0.5)',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))',
    gap: '20px',
    maxWidth: '1400px',
    margin: '0 auto',
  },
  card: {
    background: 'rgba(255,255,255,0.05)',
    borderRadius: '16px',
    padding: '25px',
    border: '1px solid rgba(255,255,255,0.1)',
  },
  cardTitle: {
    color: '#00d4ff',
    marginBottom: '20px',
    fontSize: '1.3em',
  },
  inputGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  input: {
    padding: '12px 15px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: '8px',
    color: '#fff',
    fontSize: '1em',
  },
  button: {
    padding: '12px 20px',
    background: 'rgba(0,212,255,0.2)',
    border: '1px solid rgba(0,212,255,0.3)',
    borderRadius: '8px',
    color: '#00d4ff',
    cursor: 'pointer',
    fontSize: '1em',
    transition: 'all 0.3s ease',
  },
  buttonPrimary: {
    background: 'linear-gradient(135deg, #00d4ff, #7b2cbf)',
    color: '#fff',
    border: 'none',
  },
  buttonDisabled: {
    opacity: 0.5,
    cursor: 'not-allowed',
  },
  hintText: {
    margin: '10px 0',
    fontSize: '0.9em',
  },
  versionList: {
    marginTop: '20px',
    maxHeight: '600px',
    overflowY: 'auto',
  },
  versionItem: {
    padding: '12px 15px',
    marginBottom: '8px',
    background: 'rgba(255,255,255,0.03)',
    borderRadius: '8px',
    cursor: 'pointer',
    transition: 'all 0.3s ease',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  versionItemActive: {
    background: 'rgba(0,212,255,0.2)',
    borderLeft: '3px solid #00d4ff',
  },
  versionItemPublished: {
    borderLeft: '3px solid #28a745',
  },
  versionInfo: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minWidth: 0,
  },
  versionNameRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    flexWrap: 'wrap',
  },
  versionDesc: {
    color: '#888',
    fontSize: '0.85em',
    marginTop: '4px',
  },
  versionMeta: {
    color: '#666',
    fontSize: '0.75em',
    marginTop: '4px',
  },
  publishedBadge: {
    padding: '2px 6px',
    background: 'rgba(40,167,69,0.3)',
    borderRadius: '4px',
    color: '#4ade80',
    fontSize: '0.7em',
    fontWeight: 'bold',
  },
  unpublishedBadge: {
    padding: '2px 6px',
    background: 'rgba(255,193,7,0.2)',
    borderRadius: '4px',
    color: '#ffc107',
    fontSize: '0.7em',
    fontWeight: 'bold',
  },
  versionActions: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    marginLeft: '10px',
  },
  publishBtn: {
    padding: '4px 10px',
    background: 'rgba(40,167,69,0.2)',
    border: '1px solid rgba(40,167,69,0.5)',
    borderRadius: '4px',
    color: '#4ade80',
    fontSize: '0.75em',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  deleteBtn: {
    background: 'transparent',
    border: 'none',
    color: '#ff6b6b',
    fontSize: '1.5em',
    cursor: 'pointer',
    padding: '0 5px',
    opacity: 1,
    transition: 'opacity 0.2s ease',
  },
  tabHeader: {
    display: 'flex',
    gap: '10px',
    marginBottom: '20px',
  },
  tab: {
    flex: 1,
    padding: '10px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: '8px',
    color: '#888',
    cursor: 'pointer',
  },
  tabActive: {
    background: 'rgba(0,212,255,0.2)',
    color: '#00d4ff',
    borderColor: 'rgba(0,212,255,0.3)',
  },
  uploadArea: {
    padding: '20px',
    background: 'rgba(255,255,255,0.03)',
    borderRadius: '12px',
    border: '2px dashed rgba(255,255,255,0.1)',
  },
  downloadArea: {
    padding: '20px',
    background: 'rgba(255,255,255,0.03)',
    borderRadius: '12px',
  },
  moduleSelector: {
    marginBottom: '20px',
  },
  selectLabel: {
    display: 'block',
    marginBottom: '8px',
    color: '#aaa',
  },
  select: {
    width: '100%',
    padding: '10px',
    backgroundColor: '#2a2a3a',
    border: '1px solid rgba(255,255,255,0.2)',
    borderRadius: '6px',
    color: '#fff',
    fontSize: '1em',
  },
  downloadSection: {
    marginBottom: '20px',
  },
  downloadTitle: {
    color: '#00d4ff',
    marginBottom: '12px',
    fontSize: '1em',
  },
  downloadButtons: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '10px',
  },
  downloadBtn: {
    padding: '12px',
    border: '1px solid rgba(255,255,255,0.2)',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '0.9em',
    transition: 'all 0.3s ease',
  },
  downloadBtnAvailable: {
    background: 'rgba(40,167,69,0.2)',
    color: '#4ade80',
    borderColor: 'rgba(40,167,69,0.5)',
  },
  downloadBtnUnavailable: {
    background: 'rgba(255,255,255,0.05)',
    color: '#666',
    cursor: 'not-allowed',
  },
  fileSize: {
    fontSize: '0.8em',
    opacity: 0.8,
  },
  moduleSummary: {
    marginTop: '20px',
    paddingTop: '20px',
    borderTop: '1px solid rgba(255,255,255,0.1)',
  },
  moduleList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  moduleItem: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '10px 12px',
    background: 'rgba(255,255,255,0.05)',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  moduleItemActive: {
    background: 'rgba(0,212,255,0.2)',
    borderLeft: '3px solid #00d4ff',
  },
  moduleName: {
    fontWeight: 500,
    color: '#fff',
  },
  moduleFileCount: {
    color: '#888',
    fontSize: '0.85em',
  },
  moduleRegLink: {
    marginLeft: '8px',
    padding: '2px 6px',
    background: 'rgba(0, 212, 255, 0.15)',
    borderRadius: '4px',
    fontSize: '0.9em',
    textDecoration: 'none',
    cursor: 'pointer',
  },
  noFilesMessage: {
    color: '#888',
    textAlign: 'center',
    padding: '20px',
  },
  fileInput: {
    width: '100%',
    padding: '15px',
    marginTop: '15px',
    background: 'rgba(255,255,255,0.05)',
    borderRadius: '8px',
    color: '#fff',
  },
  fileList: {
    marginTop: '15px',
    padding: '10px',
    background: 'rgba(255,255,255,0.03)',
    borderRadius: '8px',
  },
  fileTag: {
    display: 'inline-block',
    padding: '4px 10px',
    margin: '4px',
    background: 'rgba(0,212,255,0.2)',
    borderRadius: '4px',
    fontSize: '0.85em',
  },
  downloadHint: {
    color: '#888',
    margin: '15px 0 10px',
  },
  downloadNote: {
    color: '#666',
    fontSize: '0.85em',
    marginTop: '15px',
    fontStyle: 'italic',
  },
  rtlSection: {
    marginTop: '25px',
    padding: '20px',
    background: 'rgba(123, 44, 191, 0.1)',
    borderRadius: '12px',
    border: '1px solid rgba(123, 44, 191, 0.3)',
  },
  rtlTitleIcon: {
    marginRight: '8px',
  },
  rtlDescription: {
    color: '#aaa',
    fontSize: '0.9em',
    marginBottom: '15px',
  },
  rtlConfig: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '10px',
    marginBottom: '15px',
  },
  rtlConfigRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  },
  rtlLabel: {
    color: '#ccc',
    fontSize: '0.9em',
    width: '110px',
  },
  rtlSelect: {
    flex: 1,
    padding: '8px 12px',
    backgroundColor: '#2a2a3a',
    border: '1px solid rgba(255,255,255,0.2)',
    borderRadius: '6px',
    color: '#fff',
    fontSize: '0.9em',
  },
  rtlGenerateBtn: {
    width: '100%',
    padding: '14px 20px',
    background: 'linear-gradient(135deg, #7b2cbf, #00d4ff)',
    border: 'none',
    borderRadius: '8px',
    color: '#fff',
    fontSize: '1em',
    fontWeight: 'bold',
    cursor: 'pointer',
    transition: 'all 0.3s ease',
  },
  rtlFilesSection: {
    marginTop: '20px',
    padding: '15px',
    background: 'rgba(255,255,255,0.05)',
    borderRadius: '8px',
  },
  rtlFilesTitle: {
    color: '#00d4ff',
    marginBottom: '12px',
    fontSize: '0.95em',
  },
  rtlFilesList: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '8px',
  },
  rtlFileItem: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '10px 12px',
    background: 'rgba(255,255,255,0.05)',
    borderRadius: '6px',
  },
  rtlFileName: {
    color: '#fff',
    fontFamily: 'monospace',
    fontSize: '0.9em',
  },
  rtlFileSize: {
    color: '#888',
    fontSize: '0.85em',
  },
  rtlFileDownloadBtn: {
    padding: '6px 12px',
    background: 'rgba(0,212,255,0.2)',
    border: '1px solid rgba(0,212,255,0.3)',
    borderRadius: '4px',
    color: '#00d4ff',
    fontSize: '0.85em',
    cursor: 'pointer',
  },
  rtlDownloadAllBtn: {
    width: '100%',
    marginTop: '15px',
    padding: '12px',
    background: 'rgba(40,167,69,0.2)',
    border: '1px solid rgba(40,167,69,0.5)',
    borderRadius: '6px',
    color: '#4ade80',
    fontSize: '0.95em',
    cursor: 'pointer',
  },
  htmlLinkContainer: {
    textAlign: 'center',
    padding: '30px',
    background: 'rgba(0,212,255,0.1)',
    borderRadius: '12px',
    border: '1px solid rgba(0,212,255,0.3)',
  },
  htmlLinkText: {
    color: '#888',
    marginBottom: '20px',
  },
  htmlLink: {
    display: 'inline-block',
    padding: '18px 40px',
    background: 'linear-gradient(135deg, #00d4ff, #7b2cbf)',
    borderRadius: '10px',
    color: '#fff',
    textDecoration: 'none',
    fontSize: '1.2em',
    fontWeight: 'bold',
  },
  helpContent: {
    color: '#aaa',
    fontSize: '0.9em',
    lineHeight: '1.6',
  },
  helpList: {
    margin: '10px 0',
    paddingLeft: '20px',
  },
  footer: {
    textAlign: 'center',
    padding: '40px 20px',
    color: '#666',
    marginTop: '40px',
  },
  uninstantiatedList: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '10px',
  },
  uninstantiatedItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 15px',
    background: 'rgba(255, 193, 7, 0.1)',
    borderRadius: '8px',
    border: '1px solid rgba(255, 193, 7, 0.3)',
  },
  uninstantiatedInfo: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '4px',
  },
  uninstantiatedMeta: {
    color: '#888',
    fontSize: '0.85em',
    fontFamily: 'monospace',
  },
  uninstantiatedReason: {
    color: '#aaa',
    fontSize: '0.8em',
    fontStyle: 'italic',
  },
  uninstantiatedSelect: {
    padding: '8px 12px',
    backgroundColor: '#2a2a3a',
    border: '1px solid rgba(255,255,255,0.2)',
    borderRadius: '6px',
    color: '#fff',
    fontSize: '0.9em',
    minWidth: '150px',
  },
  hierarchyInfo: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '10px',
  },
  hierarchyItem: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '10px',
    background: 'rgba(255,255,255,0.05)',
    borderRadius: '6px',
  },
  hierarchyLabel: {
    color: '#888',
  },
  hierarchyValue: {
    color: '#00d4ff',
    fontWeight: 'bold',
  },
  // Modal Styles
  modalOverlay: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: 'rgba(0,0,0,0.7)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
  },
  modal: {
    background: '#1e1e2e',
    borderRadius: '16px',
    padding: '30px',
    border: '1px solid rgba(255,255,255,0.1)',
    minWidth: '350px',
    maxWidth: '450px',
  },
  modalTitle: {
    color: '#00d4ff',
    marginBottom: '15px',
    fontSize: '1.2em',
  },
  modalText: {
    color: '#aaa',
    marginBottom: '15px',
    fontSize: '0.9em',
  },
  modalInfo: {
    marginBottom: '15px',
    padding: '12px',
    background: 'rgba(255,255,255,0.03)',
    borderRadius: '8px',
    color: '#ccc',
    fontSize: '0.9em',
    lineHeight: '1.6',
  },
  modalInput: {
    width: '100%',
    padding: '12px 15px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.2)',
    borderRadius: '8px',
    color: '#fff',
    fontSize: '1em',
    marginBottom: '20px',
    boxSizing: 'border-box',
  },
  modalActions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: '10px',
  },
  modalBtnPrimary: {
    padding: '10px 20px',
    background: 'linear-gradient(135deg, #00d4ff, #7b2cbf)',
    border: 'none',
    borderRadius: '8px',
    color: '#fff',
    cursor: 'pointer',
    fontSize: '0.95em',
  },
  modalBtnSecondary: {
    padding: '10px 20px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.2)',
    borderRadius: '8px',
    color: '#aaa',
    cursor: 'pointer',
    fontSize: '0.95em',
  },
  modalBtnDanger: {
    padding: '10px 20px',
    background: 'rgba(220,53,69,0.3)',
    border: '1px solid rgba(220,53,69,0.5)',
    borderRadius: '8px',
    color: '#ff6b6b',
    cursor: 'pointer',
    fontSize: '0.95em',
  },
  // Error Box Styles
  errorBox: {
    marginTop: '20px',
    background: 'rgba(220, 53, 69, 0.1)',
    border: '1px solid rgba(220, 53, 69, 0.5)',
    borderRadius: '8px',
    overflow: 'hidden',
  },
  errorBoxHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 15px',
    background: 'rgba(220, 53, 69, 0.2)',
    borderBottom: '1px solid rgba(220, 53, 69, 0.3)',
  },
  errorBoxTitle: {
    color: '#ff6b6b',
    fontWeight: 'bold',
    fontSize: '0.95em',
  },
  errorBoxClose: {
    background: 'transparent',
    border: 'none',
    color: '#ff6b6b',
    fontSize: '1.5em',
    cursor: 'pointer',
    padding: '0 5px',
    lineHeight: 1,
  },
  errorBoxContent: {
    padding: '15px',
    maxHeight: '300px',
    overflowY: 'auto' as const,
  },
  errorBoxItem: {
    display: 'flex',
    gap: '10px',
    padding: '8px 0',
    borderBottom: '1px solid rgba(220, 53, 69, 0.1)',
    fontSize: '0.9em',
    lineHeight: 1.5,
  },
  errorBoxNumber: {
    color: '#ff6b6b',
    fontWeight: 'bold',
    minWidth: '20px',
    flexShrink: 0,
  },
  errorBoxText: {
    color: '#ffcccc',
    wordBreak: 'break-word' as const,
  },
}
