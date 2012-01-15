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
from threading import Thread
from calibre import as_unicode
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

    capabilities = frozenset(['identify'])
        # , 'cover'
    touched_fields = frozenset(['identifier:isbn',
                                'title',
                                'authors'])
        # 'identifier:isbn',
        # 'rating',
        # 'comments',
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
            self.log.exception(raw)
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
            self.log.exception(root)
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
            self.log.exception(self.authors )
        except:
            self.log.exception('Error parsing authors for url: %r' % self.url)
            self.authors = None

        mi = Metadata(self.title, self.authors)
        mi.set_identifier('biblionet', self.biblionetid)

        if self.series_index:
            mi.series_index = float(self.series_index)

        mi.source_relevance = self.relevance

        self.plugin.clean_downloaded_metadata(mi)

        print(mi)
        self.result_queue.put(mi)        
