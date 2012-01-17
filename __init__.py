#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__ = 'GPL v3'
__copyright__ = '2012, Nikos Anagnostou (nanagnos@gmail.com)'
__docformat__ = 'restructuredtext el'

import time
from urllib import quote
import socket
import re
import json
import datetime
from threading import Thread
from calibre import as_unicode
from calibre import browser
from calibre.ebooks.metadata.sources.base import Source
from calibre.utils.cleantext import clean_ascii_chars
from calibre.ebooks.metadata.book.base import Metadata
from calibre.library.comments import sanitize_comments_html
from calibre.utils.cleantext import clean_ascii_chars


class Biblionet(Source):
    name = 'Biblionet'
    description = _('Downloads Metadata and covers from biblionet.gr')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Nikos Anagnostou'
    version = (1, 0, 0)
    minimum_calibre_version = (0, 8, 4)


    capabilities = frozenset(['identify', 'cover'])
        # , 'cover'
    touched_fields = frozenset(['identifier:isbn','identifier:biblionetid','title','authors','tags', 'publisher', 'pubdate', 'series'])
        # 'identifier:isbn',
        # 'publisher',
        # 'pubdate',
        # 'tags',
        # 'series'

    supports_gzip_transfer_encoding = True

    BASE_URL = 'http://localhost/~nikan/bookmeta/index.php?isbn='

    def get_book_url(self, identifiers):
        isbn = identifiers.get('isbn', None)
        if isbn:
            return ('biblionet', isbn,
                    '%s%s' % (Biblionet.BASE_URL, isbn))

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        '''
        Note this method will retry without identifiers automatically if no
        match is found with identifiers.
        '''
        print("identify")
        print("    Identifiers are: ", identifiers)

        matches = []

        br = self.browser
        isbn = identifiers.get('isbn', None)
        if isbn:
            print("    Found isbn %s" % (isbn))
            matches.append('%s%s' % (Biblionet.BASE_URL, isbn))
        if abort.is_set():
            return

        print("    Matches are: ", matches)
        log.info("    Matches are: ", matches)

        #from calibre_plugins.biblionet.worker import Worker
        workers = [Worker(url, result_queue, br, log, i, self) for i, url in enumerate(matches)]

        for w in workers:
            w.start()
            # Don't send all requests at the same time
            time.sleep(0.1)

        while not abort.is_set():
            a_worker_is_alive = False
            for w in workers:
                w.join(0.2)
                if abort.is_set():
                    break
                if w.is_alive():
                    a_worker_is_alive = True
            if not a_worker_is_alive:
                break

        return None


    def get_cached_cover_url(self, identifiers):
        url = None
        biblionet = identifiers.get('biblionet', None)
        if biblionet is None:
            isbn = identifiers.get('isbn', None)
            if isbn is not None:
                biblionet = self.cached_isbn_to_identifier(isbn)
        if biblionet is not None:
            url = self.cached_identifier_to_cover_url(biblionet)
        return url
  
  
    def cached_identifier_to_cover_url(self, id_):
        with self.cache_lock:
          url = self._get_cached_identifier_to_cover_url(id_)
          if not url:
            # Try for a "small" image in the cache
              url = self._get_cached_identifier_to_cover_url('small/'+id_)
          return url


    def _get_cached_identifier_to_cover_url(self, id_):
        # This must only be called once we have the cache lock
        url = self._identifier_to_cover_url_cache.get(id_, None)
        if not url:
            key_prefix = id_.rpartition('/')[0]
            for key in self._identifier_to_cover_url_cache.keys():
              if key.startswith('key_prefix'):
                  return self._identifier_to_cover_url_cache[key]
        return url
    
    
    def download_cover(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(log, rq, abort, title=title, authors=authors, identifiers=identifiers)
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(key=self.identify_results_keygen(
                title=title, authors=authors, identifiers=identifiers))
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return
    
        if abort.is_set():
            return
        br = self.browser
        log('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            result_queue.put((self, cdata))
        except:
            log.exception('Failed to download cover from:', cached_url)

class Worker(Thread): # Get details

    '''
    Get book details from Biblionet book page in a separate thread
    '''

    def __init__(self, url, result_queue, browser, log, relevance, plugin, timeout=20):
        Thread.__init__(self)
        self.daemon = True
        self.url = url
        self.result_queue = result_queue
        self.log = log
        self.timeout = timeout
        self.relevance = relevance
        self.plugin = plugin
        self.browser = browser.clone_browser()
        self.cover_url = None
        self.biblionetid = None
        self.series_index= None
        self.authors=[]
        self.yr_msg1 ='No publishing year found'
        self.yr_msg2 = 'An error occured'

    def run(self):
        self.log.info("    Worker.run: self: ", self)
        try:
            self.get_details()
        except:
            self.log.exception('get_details failed for url: %r' % self.url)

    def get_details(self):
        self.log.info("    Worker.get_details:")
        self.log.info("        self:     ", self)
        self.log.info("        self.url: ", self.url)
        
        try:
            raw = self.browser.open_novisit(self.url, timeout=self.timeout).read().strip()
            self.log.info(raw)
        except Exception as e:
            if callable(getattr(e, 'getcode', None)) and e.getcode() == 404:
                self.log.error('URL malformed: %r' % self.url)
                return
            attr = getattr(e, 'args', [None])
            attr = attr if attr else [None]
            if isinstance(attr[0], socket.timeout):
                msg = 'Bookmeta for biblionet timed out. Try again later.'
                self.log.error(msg)
            else:
                msg = 'Failed to make details query: %r' % self.url
                self.log.exception(msg)
            return


        if '<title>404 - ' in raw:
            self.log.error('URL malformed: %r' % self.url)
            return

        try:
            # root = fromstring(clean_ascii_chars(raw))
            root = json.loads(raw)
            self.log.info(root)
        except:
            msg = 'Failed to parse book detail page: %r' % self.url
            self.log.exception(msg)
            return

        try:
            self.biblionetid = root['biblionetid']
        except:
            self.log.exception('Error parsing book id for url: %r' % self.url)
            self.biblionetid = None

        try:
            self.title = root['title'].strip()
        except:
            self.log.exception('Error parsing title for url: %r' % self.url)
            self.title = None
            self.series_index = None

        try:
            self.authors = [root['authors'].strip()]
            self.log.info(self.authors )
        except:
            self.log.exception('Error parsing authors for url: %r' % self.url)
            self.authors = None

        try:
            self.cover_url = root['cover_url']
            self.log.info('Parsed URL for cover:%r'%self.cover_url)
            self.plugin.cache_identifier_to_cover_url(self.biblionetid, self.cover_url)
        except:
            self.log.exception('Error parsing cover for url: %r'%self.url)
            self.has_cover = bool(self.cover_url)

        try:
            self.publisher = root['publisher']
            self.log.info('Parsed publisher:%s'%self.publisher)
        except:
            self.log.exception('Error parsing publisher for url: %r'%self.url)

        try:
            self.tags = root['categories'].replace('DDC: ','DDC:').replace('-','').split()[:-1]
            self.log.info('Parsed tags:%s'%self.tags)
        except:
            self.log.exception('Error parsing tags for url: %r'%self.url)

        try:
            self.pubdate = root['yr_published']
            self.log.info('Parsed publication date:%s'%self.pubdate)
        except:
            self.log.exception('Error parsing published date for url: %r'%self.url)
            
        mi = Metadata(self.title, self.authors)
        mi.set_identifier('biblionet', self.biblionetid)

        if self.series_index:
            try:
                mi.series_index = float(self.series_index)
            except:
                self.log.exception('Error loading series')
        if self.relevance:
            try:
                mi.source_relevance = self.relevance
            except:
                self.log.exception('Error loading relevance')
        if self.cover_url:
            try:
                mi.cover_url = self.cover_url
            except:
                self.log.exception('Error loading cover_url')
        if self.publisher:
            try:
                mi.publisher = self.publisher
            except:
                self.log.exception('Error loading publisher')
        if self.tags:
            try:
                mi.tags = self.tags
            except:
                self.log.exception('Error loading tags')
        if self.pubdate:
            try:
                if self.pubdate not in (self.yr_msg1, self.yr_msg2):
                    d = datetime.date(int(self.pubdate),1,1)
                    mi.pubdate = d
            except:
                self.log.exception('Error loading pubdate')

        self.plugin.clean_downloaded_metadata(mi)
        self.result_queue.put(mi)        
