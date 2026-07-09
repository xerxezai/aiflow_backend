"""
Threading-based async processor for MOV datasheet generation
Fallback when Celery is in EAGER mode or not available
"""
import logging
import os
import base64
import threading
import uuid
import sys
from django.core.cache import cache

logger = logging.getLogger(__name__)


def log_and_print(message):
    """Log to both logger and stderr (which Docker captures)"""
    logger.info(message)
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def process_mov_in_thread(pid_file_path, hmb_file_path, pid_filename, user_email, job_id, linelist_file_path=None):
    """
    Process MOV datasheet in background thread
    Stores result in Django cache
    
    Args:
        pid_file_path: Path to P&ID PDF
        hmb_file_path: Path to HMB PDF
        pid_filename: Original P&ID filename
        user_email: User email
        job_id: Unique job identifier
        linelist_file_path: Optional path to Line List PDF
    """
    log_and_print(f"ðŸš€ [MOV Thread {job_id[:8]}] Starting processing...")
    
    try:
        # Update progress
        cache.set(f'mov_task_{job_id}_progress', 10, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'Extracting P&ID data...', timeout=3600)
        
        # Import here to avoid issues
        import concurrent.futures
        from apps.process_datasheet.mock_extractors import MockPIDExtractor, match_lines_to_streams
        from apps.process_datasheet.hmb_vision_extractor import HMBVisionExtractor
        from apps.process_datasheet.mov_ai_mapper import MOVDatasheetAIMapper
        from apps.process_datasheet.mov_excel_generator_dynamic import MOVExcelGeneratorDynamic
        from apps.process_datasheet.tag_validator import validate_and_filter_valves
        
        # STEP 0: Upload source documents to S3 for permanent storage
        log_and_print(f'[MOV {job_id[:8]}] STEP 0: Uploading source documents to S3...')
        s3_keys = {}
        try:
            from apps.core.s3_service import S3Service
            _s3 = S3Service()
            import os as _os
            _docs_to_upload = [
                (pid_file_path, pid_filename or _os.path.basename(pid_file_path), 'pid'),
                (hmb_file_path, _os.path.basename(hmb_file_path), 'hmb'),
            ]
            if linelist_file_path:
                _docs_to_upload.append((linelist_file_path, _os.path.basename(linelist_file_path), 'linelist'))
            for _fpath, _fname, _dtype in _docs_to_upload:
                with open(_fpath, 'rb') as _fobj:
                    _s3_result = _s3.upload_file(
                        _fobj,
                        'mov_documents',
                        filename=f'{job_id[:8]}_{_dtype}_{_fname}',
                        content_type='application/pdf',
                        metadata={'job_id': job_id, 'doc_type': _dtype, 'user_email': user_email}
                    )
                if _s3_result.get('success'):
                    s3_keys[_dtype] = _s3_result.get('key')
                    log_and_print(f'[MOV {job_id[:8]}] S3 stored {_dtype}: {_s3_result.get("key")}')
                else:
                    log_and_print(f'[MOV {job_id[:8]}] S3 upload failed for {_dtype}: {_s3_result.get("error")}')
            cache.set(f'mov_task_{job_id}_s3_keys', s3_keys, timeout=3600)
        except Exception as _s3_err:
            logger.warning(f'[MOV Thread {job_id}] S3 upload step failed (non-fatal): {_s3_err}')

        # STEP 1: Extract P&ID data via soft-coded extractor chain
        # ─────────────────────────────────────────────────────────────────────
        # The chain tries multiple real extractors in order (Gemini Vision →
        # OCR+OpenAI hybrid → deterministic PyMuPDF text-regex). The first
        # strategy that returns >= MOV_EXTRACTOR_MIN_VALVES real valves wins.
        # Order / enablement is controlled by env var MOV_EXTRACTOR_CHAIN.
        # See: apps/process_datasheet/mov_extractor_chain.py
        # ─────────────────────────────────────────────────────────────────────
        log_and_print(f"📄 [MOV {job_id[:8]}] STEP 1: Extracting P&ID via extractor chain...")
        try:
            from apps.process_datasheet.mov_extractor_chain import extract_pid_with_chain

            def _on_attempt(strategy_name, message):
                # Surface progress to the polling UI via existing cache keys.
                cache.set(
                    f'mov_task_{job_id}_stage',
                    f'P&ID extraction — {message}',
                    timeout=3600,
                )
                log_and_print(f"[MOV {job_id[:8]}] {message}")

            pid_data = extract_pid_with_chain(
                pid_file_path=pid_file_path,
                pid_filename=pid_filename,
                valve_type='MOV',
                on_attempt=_on_attempt,
            )

            method = pid_data.get('extraction_method', 'unknown')
            n_valves = len(pid_data.get('valves', []))
            if n_valves == 0:
                raise ValueError('Extractor chain returned 0 valves')
            log_and_print(
                f"[MOV {job_id[:8]}] ✅ Chain extraction via '{method}': {n_valves} MOV valve(s)"
            )
        except Exception as e:
            logger.warning(f"[MOV Thread {job_id}] Extractor chain failed: {e}")
            log_and_print(f"[MOV {job_id[:8]}] Extractor chain failed. Cause: {e}")
            log_and_print(
                f"[MOV {job_id[:8]}] Falling back to MockPIDExtractor (DEMO data). "
                "Check: 1) GEMINI_API_KEY / OPENAI_API_KEY 2) PDF has visible tag circles "
                "3) Tags start with MOV/SDV/XV etc."
            )
            pid_extractor = MockPIDExtractor()
            pid_data = pid_extractor.extract_from_pdf(pid_file_path, original_filename=pid_filename)

            # Filter for MOV valves from mock data
            all_valves = pid_data.get('valves', [])
            mov_valves = [v for v in all_valves if v.get('type', '').upper() == 'MOV' or 'MOV' in v.get('tag_no', '').upper()]
            pid_data['valves'] = mov_valves
            log_and_print(f"[MOV {job_id[:8]}] Mock extraction: {len(mov_valves)} valves (DEMO data - not real)")

        # -- TAG VALIDATION (soft-coded rules in tag_validator.py) ---------------
        raw_valve_count = len(pid_data.get('valves', []))
        pid_data['valves'], tag_warnings = validate_and_filter_valves(pid_data.get('valves', []))
        for w in tag_warnings:
            log_and_print(f"[MOV {job_id[:8]}] TAG VALIDATION: {w}")
        if tag_warnings:
            demo_removed = raw_valve_count - len(pid_data['valves'])
            log_and_print(f"[MOV {job_id[:8]}] {demo_removed} DEMO/mock tag(s) removed. {len(pid_data['valves'])} real tag(s) remain.")

        # -- FIELD VALIDATION (soft-coded JSON config in mov_field_validator.py) -
        try:
            from apps.process_datasheet.mov_field_validator import validate_mov_fields
            field_result = validate_mov_fields(pid_data.get('valves', []))
            if field_result.get('enabled'):
                pid_data['valves'] = field_result['valves']
                cache.set(f'mov_task_{job_id}_field_validation',
                          field_result['summary'], timeout=3600)
                log_and_print(
                    f"[MOV {job_id[:8]}] FIELD VALIDATION: kept={field_result['summary']['kept']}"
                    f" dropped={field_result['summary']['dropped']}"
                    f" scrubbed={field_result['summary']['scrubbed']}"
                )
        except Exception as fv_err:
            log_and_print(f"[MOV {job_id[:8]}] Field validator skipped: {fv_err}")
        # -------------------------------------------------------------------------

        if len(pid_data.get('valves', [])) == 0:
            # Store a helpful user-facing error — do NOT raise so the except handler
            # can produce a clean 'failed' result rather than a raw traceback message.
            user_msg = (
                "No valve tags could be extracted from your P&ID after trying every "
                "available extractor (Gemini Vision, OpenAI Vision + multi-engine OCR, "
                "and a deterministic text-layer regex pass).\n\n"
                "Possible causes:\n"
                "1. The PDF is a fully-rasterised scan with no selectable text AND the "
                "tag circles are too blurry / low-resolution for OCR to read.\n"
                "2. Valve tags do not start with a known prefix (MOV, SDV, XV, FV, PG, "
                "XI, PT, etc.). Add new prefixes to tag_validator.VALID_TAG_PREFIXES.\n"
                "3. Both GEMINI_API_KEY and OPENAI_API_KEY are missing or over quota — "
                "the chain falls through to the offline regex pass which only works on "
                "PDFs with a text layer.\n"
                "4. The valves visible on the drawing are not of MOV type.\n\n"
                "Tip: try uploading a higher-resolution PDF, or export your CAD drawing "
                "with the text layer preserved (not 'flattened to image')."
            )
            log_and_print(f"[MOV {job_id[:8]}] FAILED: {user_msg}")
            error_result = {'success': False, 'error': user_msg}
            cache.set(f'mov_task_{job_id}_result', error_result, timeout=3600)
            cache.set(f'mov_task_{job_id}_stage', 'Error: No valve tags found', timeout=3600)
            return error_result
        # -------------------------------------------------------------------------

        cache.set(f'mov_task_{job_id}_progress', 30, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'Extracting HMB data with Vision AI (this may take 2-5 minutes)...', timeout=3600)

        # ── Soft-coded HMB extraction timeout ──────────────────────────────────
        # Increase HMB_VISION_TIMEOUT_SEC to allow longer AI processing.
        # With Gemini Flash processing up to 15 pages this may need 5–8 minutes.
        HMB_VISION_TIMEOUT_SEC = int(os.getenv('HMB_VISION_TIMEOUT_SEC', '480'))  # 8 min default
        # ────────────────────────────────────────────────────────────────────────

        # STEP 2: Extract HMB data using Vision
        # NOTE: signal.SIGALRM only works in main thread; use concurrent.futures instead.
        def _run_hmb_extraction():
            return HMBVisionExtractor().extract_from_pdf(hmb_file_path)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _hmb_pool:
                _hmb_future = _hmb_pool.submit(_run_hmb_extraction)
                hmb_data = _hmb_future.result(timeout=HMB_VISION_TIMEOUT_SEC)
            log_and_print(f"[MOV {job_id[:8]}] HMB extracted: {len(hmb_data.get('streams', []))} streams")
            if not hmb_data.get('streams'):
                raise ValueError("HMB Vision returned 0 streams")
        except concurrent.futures.TimeoutError:
            log_and_print(f"[MOV {job_id[:8]}] HMB Vision timed out (>{HMB_VISION_TIMEOUT_SEC}s), using mock data")
            from apps.process_datasheet.mock_extractors import MockHMBExtractor
            hmb_data = MockHMBExtractor().extract_from_pdf(hmb_file_path)
        except Exception as e:
            logger.warning(f"[MOV Thread {job_id}] HMB Vision failed: {e}")
            log_and_print(f"[MOV {job_id[:8]}] HMB Vision failed ({e}), using mock data")
            from apps.process_datasheet.mock_extractors import MockHMBExtractor
            hmb_data = MockHMBExtractor().extract_from_pdf(hmb_file_path)

        # STEP 2.5: Extract Line List data if provided (optional)
        line_list_data = None
        if linelist_file_path:
            cache.set(f'mov_task_{job_id}_stage', 'Extracting Line List data...', timeout=3600)
            log_and_print(f"📋 [MOV {job_id[:8]}] STEP 2.5: Extracting Line List...")
            try:
                vision_extractor = HMBVisionExtractor()
                line_list_data = vision_extractor.extract_from_pdf(linelist_file_path)
                log_and_print(f"✅ [MOV {job_id[:8]}] Line List extracted")
            except Exception as e:
                logger.warning(f"[MOV Thread {job_id}] Line List extraction failed: {e}")
                log_and_print(f"⚠️ [MOV {job_id[:8]}] Line List extraction failed, continuing without it...")
                line_list_data = None
        
        cache.set(f'mov_task_{job_id}_stage', 'Matching P&ID and HMB data...', timeout=3600)
        
        # STEP 3: Match lines
        log_and_print(f"ðŸ”— [MOV {job_id[:8]}] STEP 3: Matching lines...")
        line_context = match_lines_to_streams(pid_data, hmb_data)
        log_and_print(f"âœ… [MOV {job_id[:8]}] Lines matched")

        # Cross-document interlinking audit
        _all_valves = pid_data.get('valves', [])
        _hmb_line_nos = {s.get('line_no', '').strip() for s in hmb_data.get('streams', []) if s.get('line_no')}
        _linked = [v for v in _all_valves if v.get('line_no', '').strip() in _hmb_line_nos]
        _unlinked = [v for v in _all_valves if v.get('line_no', '').strip() not in _hmb_line_nos]
        _audit = {
            'total_valves': len(_all_valves),
            'linked_to_hmb': len(_linked),
            'unlinked_valves': [v.get('tag_no', v.get('tag')) for v in _unlinked],
            'hmb_streams': len(hmb_data.get('streams', [])),
            'linelist_provided': linelist_file_path is not None,
            's3_documents_stored': list(s3_keys.keys()),
        }
        cache.set(f'mov_task_{job_id}_doc_links', _audit, timeout=3600)
        log_and_print(f'[MOV {job_id[:8]}] Interlinking: {len(_linked)}/{len(_all_valves)} valves linked to HMB streams')
        if _unlinked:
            log_and_print(f'[MOV {job_id[:8]}] Unlinked valves (no matching HMB line): {[v.get("tag_no", v.get("tag")) for v in _unlinked]}')
        
        cache.set(f'mov_task_{job_id}_progress', 75, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'AI intelligent mapping...', timeout=3600)
        
        # STEP 4: AI Mapping with fallback to basic mapping
        log_and_print(f"ðŸ¤– [MOV {job_id[:8]}] STEP 4: AI intelligent mapping...")
        try:
            mapper = MOVDatasheetAIMapper()
            mapped_data = mapper.map_pid_hmb_to_datasheet(pid_data, hmb_data, line_context, line_list_data)
            
            # Check if AI mapping actually produced results
            if not mapped_data.get('valves') or len(mapped_data.get('valves', [])) == 0:
                raise ValueError(f"AI mapping returned 0 valves. Error: {mapped_data.get('error', 'Unknown')}")
                
            log_and_print(f"âœ… [MOV {job_id[:8]}] AI mapping complete: {len(mapped_data.get('valves', []))} valves mapped")
        except Exception as e:
            logger.warning(f"[MOV Thread {job_id}] AI mapping failed, using basic mapping: {e}")
            log_and_print(f"âš ï¸ [MOV {job_id[:8]}] AI failed, using basic mapping from P&ID data...")
            
            # Fallback: Create basic mapped data from P&ID valves
            # Pull HMB values from the first matching stream (or first stream as best-effort)
            # ─── Soft-coded: extend _hmb_stream_lookup to add more matching keys ───
            _hmb_streams = hmb_data.get('streams', [])
            def _best_hmb_stream(line_no):
                """Return the HMB stream whose line_no best matches the valve's line_no."""
                if line_no:
                    for s in _hmb_streams:
                        if s.get('line_no') and line_no in str(s.get('line_no', '')):
                            return s
                return _hmb_streams[0] if _hmb_streams else {}

            mapped_valves = []
            for valve in pid_data.get('valves', []):
                _hs = _best_hmb_stream(valve.get('line_no', ''))
                # Temperature — prefer HMB stream; valve fields only as last resort
                _t_min    = _hs.get('temp_min')    or valve.get('temp_min', '')
                _t_normal = _hs.get('temp_normal') or _hs.get('temp_max') or _hs.get('temp_min') or valve.get('temp_max', '')
                _t_max    = _hs.get('temp_max')    or _hs.get('temp_min') or valve.get('temp_max', '')
                _t_unit   = _hs.get('temp_unit')   or '°C'
                _dt_min   = _hs.get('design_temp_min') or _t_min
                _dt_max   = _hs.get('design_temp_max') or _t_max
                # Pressure — prefer HMB stream
                _p_normal = _hs.get('pressure_normal') or valve.get('pressure', '')
                _p_unit   = _hs.get('pressure_unit')   or 'barg'
                _dp_max   = _hs.get('pressure_design') or valve.get('design_pressure', '')
                mapped_valve = {
                    'tag_no': valve.get('tag_no', valve.get('tag', 'UNKNOWN')),
                    'tag': valve.get('tag', valve.get('tag_no', 'UNKNOWN')),
                    'pid_no': pid_data.get('drawing_info', {}).get('pid_no', 'UNKNOWN'),
                    'line_no': valve.get('line_no', ''),
                    'service': valve.get('service', valve.get('description', '')),
                    'piping_class': valve.get('piping_class', ''),
                    'fluid': _hs.get('fluid', 'See HMB'),
                    'phase': _hs.get('phase', 'TBD'),
                    'operating_pressure_normal': _p_normal,
                    'operating_pressure_unit': _p_unit,
                    'operating_temp_min': _t_min,
                    'operating_temp_normal': _t_normal,
                    'operating_temp_max': _t_max,
                    'operating_temp_unit': _t_unit,
                    # Keep single-value form for backwards compat AND provide split fields
                    'design_pressure': _dp_max,
                    'design_pressure_min': valve.get('design_pressure_min', '0'),
                    'design_pressure_max': _dp_max,
                    'design_temp_min': _dt_min,
                    'design_temp_max': _dt_max,
                    'sour_service': valve.get('sour_service', 'No'),
                    # Use P&ID notes field for special conditions; default to 'None'
                    'special_conditions': valve.get('special_conditions') or valve.get('notes') or 'None',
                }
                mapped_valves.append(mapped_valve)
            
            mapped_data = {
                'valves': mapped_valves,
                'drawing_info': pid_data.get('drawing_info', {}),
                'mapping_method': 'basic_fallback'
            }
            log_and_print(f"âœ… [MOV {job_id[:8]}] Basic mapping complete: {len(mapped_valves)} valves mapped")
        
        cache.set(f'mov_task_{job_id}_progress', 90, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'Generating Excel datasheet...', timeout=3600)
        
        # STEP 5: Generate Excel
        log_and_print(f"ðŸ“ˆ [MOV {job_id[:8]}] STEP 5: Generating Excel...")
        generator = MOVExcelGeneratorDynamic()
        excel_buffer = generator.generate_datasheet(mapped_data)
        excel_bytes = excel_buffer.getvalue()
        excel_base64 = base64.b64encode(excel_bytes).decode('utf-8')
        log_and_print(f"âœ… [MOV {job_id[:8]}] Excel generated: {len(excel_bytes)} bytes")
        
        # STEP 6: Generate HTML
        from apps.process_datasheet.mov_streams_view import generate_html_preview
        html_preview = generate_html_preview(mapped_data)
        
        # Store result
        result = {
            'success': True,
            'html_preview': html_preview,
            'excel_file': excel_base64,
            'filename': f'MOV_Datasheet_{pid_data.get("drawing_info", {}).get("pid_no", "Unknown")}.xlsx',
            's3_keys': s3_keys,
            'doc_links': _audit,
        }
        
        cache.set(f'mov_task_{job_id}_result', result, timeout=3600)
        cache.set(f'mov_task_{job_id}_progress', 100, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'Complete!', timeout=3600)
        
        log_and_print(f"âœ…âœ…âœ… [MOV {job_id[:8]}] COMPLETE! Datasheet ready.")
        return result
        
    except Exception as e:
        logger.error(f"[MOV Thread {job_id}] âŒ Error: {e}", exc_info=True)
        
        # Provide more user-friendly error messages
        error_message = str(e)
        if 'insufficient_quota' in error_message or '429' in error_message or 'exceeded your current quota' in error_message:
            error_message = "OpenAI API quota exceeded. Please add credits at https://platform.openai.com/account/billing to continue processing."
        elif 'rate_limit' in error_message.lower():
            error_message = "OpenAI API rate limit reached. Please wait a moment and try again."
        
        error_result = {
            'success': False,
            'error': error_message
        }
        cache.set(f'mov_task_{job_id}_result', error_result, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', f'Error: {error_message}', timeout=3600)
        return error_result
    
    finally:
        # Cleanup temp files
        try:
            if os.path.exists(pid_file_path):
                os.remove(pid_file_path)
            if os.path.exists(hmb_file_path):
                os.remove(hmb_file_path)
        except Exception as e:
            logger.warning(f"[MOV Thread {job_id}] Cleanup error: {e}")


def start_async_processing(pid_file_path, hmb_file_path, pid_filename, user_email, linelist_file_path=None):
    """
    Start async processing in a background thread
    
    Returns:
        job_id: Unique identifier for tracking
    """
    job_id = str(uuid.uuid4())
    
    # Start thread
    thread = threading.Thread(
        target=process_mov_in_thread,
        args=(pid_file_path, hmb_file_path, pid_filename, user_email, job_id, linelist_file_path),
        daemon=True  # Thread will not prevent program exit
    )
    thread.start()
    
    logger.info(f"[MOV] Started background thread with job_id: {job_id}, linelist: {bool(linelist_file_path)}")
    return job_id
